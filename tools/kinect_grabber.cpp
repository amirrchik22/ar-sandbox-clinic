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
// Служебные сообщения идут в стандартный поток ошибок, чтобы не портить кадры.
//
// Сборка: tools/build_grabber.sh (macOS, Linux); под Windows — по шагам в README.md,
// раздел «Датчик на Windows».
// Запуск: build/kinect_grabber [--pipeline cpu|opencl] [--frames N] [--serial S]
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
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

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

  for (int i = 1; i < argc; i++) {
    std::string a = argv[i];
    if (a == "--pipeline" && i + 1 < argc) pipeline_name = argv[++i];
    else if (a == "--serial" && i + 1 < argc) serial = argv[++i];
    else if (a == "--frames" && i + 1 < argc) max_frames = atol(argv[++i]);
    else if (a == "--quiet") quiet = true;
    else if (a == "--help" || a == "-h") {
      fprintf(stderr,
              "kinect_grabber — захват глубины с Kinect v2 в стандартный вывод\n"
              "  --pipeline cpu|opencl   обработчик глубины (по умолчанию cpu)\n"
              "  --serial SERIAL         конкретный датчик, если их несколько\n"
              "  --frames N              остановиться после N кадров\n"
              "  --quiet                 меньше служебных сообщений\n");
      return 0;
    } else {
      fprintf(stderr, "grabber: непонятный параметр '%s'\n", a.c_str());
      return 2;
    }
  }

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

  libfreenect2::SyncMultiFrameListener listener(libfreenect2::Frame::Depth);
  dev->setIrAndDepthFrameListener(&listener);
  if (!dev->startStreams(false, true)) {    // только глубина, цвет не нужен
    fprintf(stderr, "grabber: поток не стартовал\n");
    dev->close();
    return 1;
  }
  fprintf(stderr, "grabber: поток пошёл, датчик %s, обработчик %s\n",
          dev->getSerialNumber().c_str(), pipeline_name.c_str());
  fflush(stderr);

  libfreenect2::FrameMap frames;
  uint32_t n = 0;
  int lost = 0;
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
    if (!quiet && n % 150 == 0) { fprintf(stderr, "grabber: кадров отдано %u\n", n); fflush(stderr); }
    if (max_frames > 0 && n >= static_cast<uint32_t>(max_frames)) break;
  }

  fprintf(stderr, "grabber: остановка, кадров отдано %u\n", n);
  dev->stop();
  dev->close();
  return 0;
}
