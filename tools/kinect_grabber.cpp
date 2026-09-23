// Захват кадров глубины с Kinect v2 и выдача их в стандартный вывод сырым потоком.
//
// Зачем отдельная программа: libfreenect2 — библиотека на C++, а весь остальной
// код песочницы на Python. Самый простой и надёжный мост — труба (pipe):
// эта программа печатает кадры в стандартный вывод, Python-сервис запускает её
// как подпроцесс и читает поток. Никакой разделяемой памяти и привязки к системе,
// на Ubuntu переносится без изменений.
//
// Формат одного кадра (всё в порядке байтов машины, little-endian на нашем железе):
//   4 байта  — магическое слово "KIN2"
//   4 байта  — номер кадра, uint32, с нуля
//   4 байта  — ширина, uint32  (512)
//   4 байта  — высота, uint32  (424)
//   512*424*4 байта — расстояния float32 в миллиметрах; 0 = нет данных
//
// С ключом --color в тот же поток добавляются цветные кадры — у них свой
// заголовок, и по первым четырём байтам всегда видно, кадр какого рода пришёл:
//   4 байта  — магическое слово "KINC"
//   4 байта  — номер цветного кадра, uint32, с нуля
//   4 байта  — ширина, uint32
//   4 байта  — высота, uint32
//   4 байта  — формат, uint32: 1 = BGR, три байта на точку
//   4 байта  — длина данных, uint32 (ширина*высота*3)
//   длина байт — сами точки
//
// Глубина в этом потоке главнее цвета: кадры глубины не пропускаются никогда,
// а цветные берутся не чаще --color-fps и роняются, если не успели. Так сделано
// нарочно: занятие ведётся по рельефу, видео с камеры — дело десятое.
//
// Служебные сообщения идут в стандартный поток ошибок, чтобы не портить кадры.
//
// Сборка: tools/build_grabber.sh (macOS, Linux); под Windows — по шагам в README.md,
// раздел «Датчик на Windows».
// Запуск: build/kinect_grabber [--pipeline cpu|opencl] [--frames N] [--serial S]
//         [--color] [--color-fps N] [--color-scale 1|2|4]
//
// Про цвет. Камера датчика даёт 1920x1080 и 30 кадров в секунду, картинка идёт
// по USB уже сжатой в JPEG; распаковывает её сама libfreenect2 — на Маке через
// VideoToolbox (аппаратно, включено при сборке), на Linux через TurboJPEG.
// Мы ничего своего не распаковываем: берём готовый кадр, уменьшаем в --color-scale
// раз (по умолчанию вдвое, 960x540) и отдаём тремя байтами на точку. Уменьшение
// нужно, чтобы не гнать 250 МБ/с через трубу: вдвое — это уже 1,5 МБ на кадр.
//
// ВАЖНО (проверено живьём 23.09 на macOS 26): обработчик глубины OpenCL на этом
// Маке выдаёт мусор — 0 % годных точек. Работает CpuPacketPipeline, он и стоит
// по умолчанию. На Ubuntu с дискретной видеокартой можно пробовать opencl.
// Датчик не работает через USB-хабы: нужен прямой порт USB 3.0.

#include <libfreenect2/libfreenect2.hpp>
#include <libfreenect2/frame_listener_impl.h>
#include <libfreenect2/packet_pipeline.h>
#include <libfreenect2/logger.h>

#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

// Единственное, что тут зависит от системы, — запись в стандартный вывод и пауза.
// На Windows нет unistd.h, зато есть _write и Sleep; а ещё стандартный вывод там
// по умолчанию текстовый и молча подменяет байт 0x0A на пару 0x0D 0x0A — это
// испортило бы каждый кадр, поэтому ниже, в main, переводим его в двоичный режим.
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#include <windows.h>
#define SANDBOX_SLEEP_S(n) Sleep((n) * 1000)
#else
#include <unistd.h>
#define SANDBOX_SLEEP_S(n) sleep(n)
#endif

static volatile sig_atomic_t g_stop = 0;

// Свой журнал: всё, что печатает библиотека, уходит в стандартный поток ошибок.
// Это обязательно: обычный журнал libfreenect2 пишет в стандартный вывод, а там
// у нас идут кадры — его строки ломали бы поток. Именно на этом мы обожглись:
// Python-сервис видел вместо начала кадра строку "[Info] ...", считал поток
// сбившимся, перезапускал захват, а старая копия программы продолжала держать
// датчик, и новая уже не могла его открыть.
class StderrLogger : public libfreenect2::Logger {
public:
  explicit StderrLogger(Level level) { level_ = level; }
  void log(Level level, const std::string &message) override {
    fprintf(stderr, "[%s] %s\n", level2str(level).c_str(), message.c_str());
    fflush(stderr);
  }
};

// Приёмник цветных кадров. Держит ровно один, самый свежий: пришёл новый —
// прошлый выбрасываем. Это и есть обещание «цвет можно ронять»: даже если
// основной цикл занят глубиной, память не растёт и датчик не ждёт.
//
// Про владение кадром: onNewFrame возвращает true, когда кадр забираем себе,
// и тогда удалить его обязаны мы; вернём false — библиотека удалит сама.
class ColorSink : public libfreenect2::FrameListener {
public:
  bool onNewFrame(libfreenect2::Frame::Type type, libfreenect2::Frame *frame) override {
    if (type != libfreenect2::Frame::Color) return false;    // чужой кадр не берём
    libfreenect2::Frame *old = NULL;
    {
      std::lock_guard<std::mutex> guard(mutex_);
      old = pending_;
      pending_ = frame;
      if (old != NULL) dropped_++;
      taken_++;
    }
    delete old;                                              // прошлый кадр не пригодился
    return true;
  }

  // Забрать свежий кадр; NULL — нового не было. Удалять — тому, кто забрал.
  libfreenect2::Frame *take() {
    std::lock_guard<std::mutex> guard(mutex_);
    libfreenect2::Frame *frame = pending_;
    pending_ = NULL;
    return frame;
  }

  void clear() { delete take(); }
  unsigned long dropped() const { return dropped_; }
  unsigned long taken() const { return taken_; }

  ~ColorSink() override { clear(); }

private:
  std::mutex mutex_;
  libfreenect2::Frame *pending_ = NULL;
  unsigned long dropped_ = 0;
  unsigned long taken_ = 0;
};

// Обработчик глубины создаём заново на каждую попытку открытия: библиотека
// забирает его себе и удаляет сама, даже когда открыть датчик не вышло.
static libfreenect2::PacketPipeline *make_pipeline(const std::string &name) {
  if (name == "opencl") {
#ifdef LIBFREENECT2_WITH_OPENCL_SUPPORT
    return new libfreenect2::OpenCLPacketPipeline();
#endif
  }
  return new libfreenect2::CpuPacketPipeline();
}

static void on_signal(int) { g_stop = 1; }   // мягкая остановка: доигрываем кадр и выходим

// Пишет ровно n байт в стандартный вывод. Возвращает false, если труба закрылась
// (например, Python-сервис завершился) — тогда программа спокойно останавливается.
static bool write_all(const void *data, size_t n) {
  const char *p = static_cast<const char *>(data);
  while (n > 0) {
#ifdef _WIN32
    int got = ::_write(1, p, static_cast<unsigned int>(n));
#else
    ssize_t got = ::write(STDOUT_FILENO, p, n);
#endif
    if (got <= 0) {
      if (got < 0 && errno == EINTR) continue;
      return false;
    }
    p += got;
    n -= static_cast<size_t>(got);
  }
  return true;
}

// Уменьшает цветной кадр в scale раз и отдаёт его в трубу тремя байтами на точку.
//
// Кадр приходит от библиотеки как BGRX — четыре байта на точку, четвёртый пустой.
// Мы усредняем квадрат scale x scale и выбрасываем пустой байт: кадр худеет в
// scale*scale*4/3 раз. Усреднение, а не выбрасывание точек, — чтобы на песке не
// появилась рябь и мелкий рисунок не пропал.
static bool write_color(const libfreenect2::Frame *frame, int scale, uint32_t num,
                        std::vector<unsigned char> &buf) {
  if (frame == NULL || frame->data == NULL) return true;       // нечего отдавать — не беда
  if (frame->format != libfreenect2::Frame::BGRX &&
      frame->format != libfreenect2::Frame::RGBX) return true;  // чужой формат пропускаем
  const size_t w = frame->width, h = frame->height;
  if (w == 0 || h == 0) return true;

  const size_t out_w = w / static_cast<size_t>(scale);
  const size_t out_h = h / static_cast<size_t>(scale);
  if (out_w == 0 || out_h == 0) return true;
  buf.resize(out_w * out_h * 3);

  const unsigned char *src = frame->data;
  const size_t src_stride = w * 4;
  const int s = scale;
  const unsigned int area = static_cast<unsigned int>(s) * static_cast<unsigned int>(s);
  for (size_t y = 0; y < out_h; y++) {
    unsigned char *dst = &buf[(y * out_w) * 3];
    for (size_t x = 0; x < out_w; x++) {
      unsigned int b = 0, g = 0, r = 0;
      for (int dy = 0; dy < s; dy++) {
        const unsigned char *row = src + (y * s + dy) * src_stride + (x * s) * 4;
        for (int dx = 0; dx < s; dx++) {
          b += row[0]; g += row[1]; r += row[2];
          row += 4;
        }
      }
      dst[0] = static_cast<unsigned char>(b / area);
      dst[1] = static_cast<unsigned char>(g / area);
      dst[2] = static_cast<unsigned char>(r / area);
      dst += 3;
    }
  }
  // Порядок байтов в кадре RGBX обратный — меняем местами первый и третий.
  if (frame->format == libfreenect2::Frame::RGBX) {
    for (size_t i = 0; i + 2 < buf.size(); i += 3) {
      unsigned char t = buf[i]; buf[i] = buf[i + 2]; buf[i + 2] = t;
    }
  }

  uint32_t cw = static_cast<uint32_t>(out_w);
  uint32_t ch = static_cast<uint32_t>(out_h);
  uint32_t fmt = 1;                                  // 1 = BGR, три байта на точку
  uint32_t len = static_cast<uint32_t>(buf.size());
  return write_all("KINC", 4) && write_all(&num, 4) && write_all(&cw, 4)
         && write_all(&ch, 4) && write_all(&fmt, 4) && write_all(&len, 4)
         && write_all(buf.data(), buf.size());
}

int main(int argc, char **argv) {
#ifdef _WIN32
  // Обязательно и до первого кадра: иначе Windows превратит каждый байт 0x0A
  // внутри кадра в 0x0D 0x0A, кадр раздуется и Python увидит мусор вместо "KIN2".
  _setmode(_fileno(stdout), _O_BINARY);
#endif
  std::string pipeline_name = "cpu";   // по умолчанию — проверенный обработчик
  std::string serial;                  // пусто = первый найденный датчик
  long max_frames = 0;                 // 0 = без ограничения
  bool quiet = false;
  bool color_on = false;               // по умолчанию цвет не пишем вовсе
  double color_fps = 10.0;             // не чаще этого отдаём цветные кадры
  int color_scale = 2;                 // во сколько раз уменьшаем цветной кадр

  for (int i = 1; i < argc; i++) {
    std::string a = argv[i];
    if (a == "--pipeline" && i + 1 < argc) pipeline_name = argv[++i];
    else if (a == "--serial" && i + 1 < argc) serial = argv[++i];
    else if (a == "--frames" && i + 1 < argc) max_frames = atol(argv[++i]);
    else if (a == "--quiet") quiet = true;
    else if (a == "--color") color_on = true;
    else if (a == "--color-fps" && i + 1 < argc) color_fps = atof(argv[++i]);
    else if (a == "--color-scale" && i + 1 < argc) color_scale = atoi(argv[++i]);
    else if (a == "--help" || a == "-h") {
      fprintf(stderr,
              "kinect_grabber — захват глубины с Kinect v2 в стандартный вывод\n"
              "  --pipeline cpu|opencl   обработчик глубины (по умолчанию cpu)\n"
              "  --serial SERIAL         конкретный датчик, если их несколько\n"
              "  --frames N              остановиться после N кадров\n"
              "  --quiet                 меньше служебных сообщений\n"
              "  --color                 отдавать ещё и цветные кадры (по умолчанию нет)\n"
              "  --color-fps N           не чаще N цветных кадров в секунду (по умолчанию 10)\n"
              "  --color-scale 1|2|4     уменьшить цветной кадр (по умолчанию 2 -> 960x540)\n");
      return 0;
    } else {
      fprintf(stderr, "grabber: непонятный параметр '%s'\n", a.c_str());
      return 2;
    }
  }

  if (color_scale != 1 && color_scale != 2 && color_scale != 4) {
    fprintf(stderr, "grabber: --color-scale бывает 1, 2 или 4, взял 2\n");
    color_scale = 2;
  }
  if (color_fps <= 0.0 || color_fps > 30.0) color_fps = 10.0;

  signal(SIGINT, on_signal);
  signal(SIGTERM, on_signal);
  signal(SIGHUP, on_signal);
  signal(SIGPIPE, SIG_IGN);            // закрытую трубу ловим сами, по ошибке write

  libfreenect2::setGlobalLogger(new StderrLogger(
      quiet ? libfreenect2::Logger::Error : libfreenect2::Logger::Warning));

  libfreenect2::Freenect2 freenect2;
  if (freenect2.enumerateDevices() == 0) {
    fprintf(stderr, "grabber: датчик не найден (проверьте питание и прямой порт USB 3.0, без хаба)\n");
    return 1;
  }
  const std::string serial_arg = serial;         // пусто = берём первый найденный
  if (serial.empty()) serial = freenect2.getDefaultDeviceSerialNumber();

#ifndef LIBFREENECT2_WITH_OPENCL_SUPPORT
  if (pipeline_name == "opencl") {
    fprintf(stderr, "grabber: сборка без OpenCL, беру cpu\n");
    pipeline_name = "cpu";
  }
#endif

  // Датчик часто не открывается с первой попытки: после предыдущего запуска он
  // висит в неприбранном состоянии, попытка открытия сбрасывает его по USB, и
  // он появляется на шине уже под другим адресом. Внутри одного процесса ловить
  // его дальше бесполезно — библиотека держит старый указатель на устройство и
  // сброс раз за разом отвечает "нет такого". Проверено живьём: помогает именно
  // новый процесс. Поэтому здесь всего две попытки, а дальше программа выходит с
  // ошибкой, и Python-сервис запускает её заново — с чистого листа это работает.
  libfreenect2::Freenect2Device *dev = NULL;
  for (int attempt = 1; attempt <= 2 && !g_stop; attempt++) {
    if (attempt > 1) {
      SANDBOX_SLEEP_S(3);                        // даём датчику вернуться на шину
      if (freenect2.enumerateDevices() == 0) {
        fprintf(stderr, "grabber: попытка %d — датчика пока не видно\n", attempt);
        fflush(stderr);
        continue;
      }
      if (serial_arg.empty()) serial = freenect2.getDefaultDeviceSerialNumber();
    }
    dev = freenect2.openDevice(serial, make_pipeline(pipeline_name));
    if (dev) break;
    fprintf(stderr, "grabber: попытка %d — датчик %s не открылся, пробую снова\n",
            attempt, serial.c_str());
    fflush(stderr);
  }
  if (!dev) {
    fprintf(stderr, "grabber: датчик %s не открылся\n", serial.c_str());
    return 1;
  }

  // Два приёмника нарочно разные. Глубину берём через SyncMultiFrameListener,
  // и он ждёт только глубину — попроси мы у него ещё и цвет, он ждал бы оба
  // кадра сразу, и медленный цвет тормозил бы рельеф. Цвет идёт мимо, своим
  // приёмником, который хранит один свежий кадр и молча роняет остальные.
  libfreenect2::SyncMultiFrameListener listener(libfreenect2::Frame::Depth);
  ColorSink color_sink;
  dev->setIrAndDepthFrameListener(&listener);
  if (color_on) dev->setColorFrameListener(&color_sink);
  if (!dev->startStreams(color_on, true)) {
    fprintf(stderr, "grabber: поток не стартовал\n");
    dev->close();
    return 1;
  }
  fprintf(stderr, "grabber: поток пошёл, датчик %s, обработчик %s, цвет %s\n",
          dev->getSerialNumber().c_str(), pipeline_name.c_str(),
          color_on ? "включён" : "выключен");
  fflush(stderr);

  libfreenect2::FrameMap frames;
  uint32_t n = 0;
  uint32_t color_n = 0;
  int lost = 0;
  std::vector<unsigned char> color_buf;         // буфер уменьшенного кадра, растёт один раз
  const double color_period = 1.0 / color_fps;  // не чаще этого отдаём цвет
  auto color_last = std::chrono::steady_clock::now() -
                    std::chrono::seconds(1);    // первый цветной кадр отдаём сразу
  while (!g_stop) {
    if (!listener.waitForNewFrame(frames, 10 * 1000)) {   // 10 с ожидания кадра
      fprintf(stderr, "grabber: кадры пропали, жду дальше (%d)\n", ++lost);
      fflush(stderr);
      if (lost >= 3) break;
      continue;
    }
    lost = 0;
    libfreenect2::Frame *dp = frames[libfreenect2::Frame::Depth];
    uint32_t w = static_cast<uint32_t>(dp->width);
    uint32_t h = static_cast<uint32_t>(dp->height);
    bool ok = write_all("KIN2", 4) && write_all(&n, 4) && write_all(&w, 4) && write_all(&h, 4)
              && write_all(dp->data, static_cast<size_t>(w) * h * 4);
    listener.release(frames);
    if (!ok) {
      fprintf(stderr, "grabber: труба закрыта, останавливаюсь\n");
      break;
    }
    n++;

    // Цвет — после глубины и только если подошло время. Кадр, который не
    // подошёл, просто удаляем: терять цветные кадры нам разрешено.
    if (color_on) {
      libfreenect2::Frame *cf = color_sink.take();
      if (cf != NULL) {
        auto now_t = std::chrono::steady_clock::now();
        double since = std::chrono::duration<double>(now_t - color_last).count();
        if (since >= color_period) {
          if (!write_color(cf, color_scale, color_n, color_buf)) {
            delete cf;
            fprintf(stderr, "grabber: труба закрыта, останавливаюсь\n");
            break;
          }
          color_last = now_t;
          color_n++;
        }
        delete cf;                                // кадр наш — удаляем сами
      }
    }

    if (!quiet && n % 150 == 0) { fprintf(stderr, "grabber: кадров отдано %u\n", n); fflush(stderr); }
    if (max_frames > 0 && n >= static_cast<uint32_t>(max_frames)) break;
  }

  if (color_on) {
    fprintf(stderr, "grabber: цветных кадров отдано %u, уронено %lu из %lu\n",
            color_n, color_sink.dropped(), color_sink.taken());
    color_sink.clear();
    dev->setColorFrameListener(NULL);
  }
  fprintf(stderr, "grabber: остановка, кадров отдано %u\n", n);
  dev->stop();
  dev->close();
  return 0;
}
