// Живая картинка без мигания.
//
// Сначала пробуем поток MJPEG: браузер сам меняет кадры в одном соединении.
// Если поток не пошёл (такое бывает в старых браузерах), переключаемся на
// обычные кадры по одному. Чтобы не было мигания, кадры грузятся в невидимую
// картинку и показываются только после полной загрузки.
//
// Возвращает пульт управления картинкой:
//   stop()    — отцепиться от потока (уходим с экрана, не грузим сеть)
//   start()   — снова показывать живую картинку
//   freeze()  — заморозка: показываем последний кадр и не обновляем его
//   thaw()    — снять заморозку
//
// Картинки никогда не получают src="" — пустой src браузер понимает как адрес
// самой страницы и ругается в консоли. Отцепляемся через removeAttribute.

function liveImage(box, streamUrl, frameUrl, fps) {
  const a = document.createElement('img');
  const b = document.createElement('img');
  const still = document.createElement('img');      // замороженный кадр
  a.alt = b.alt = still.alt = 'Картинка с песочницы';
  b.className = 'hidden';
  still.className = 'hidden';
  box.appendChild(a);
  box.appendChild(b);
  box.appendChild(still);

  let shown = a;
  let hidden = b;
  let polling = false;
  let stopped = false;
  let frozen = false;
  let timer = null;
  let guard = null;

  function swap() {
    hidden.classList.remove('hidden');
    shown.classList.add('hidden');
    const t = shown;
    shown = hidden;
    hidden = t;
  }

  function pollOnce() {
    if (stopped || frozen) return;
    hidden.onload = () => {
      if (stopped || frozen) return;
      swap();
      timer = setTimeout(pollOnce, Math.max(0, 1000 / fps));
    };
    hidden.onerror = () => {
      if (stopped || frozen) return;
      timer = setTimeout(pollOnce, 1000);
    };
    hidden.src = frameUrl + (frameUrl.indexOf('?') < 0 ? '?' : '&') + 't=' + Date.now();
  }

  function startPolling() {
    if (polling || stopped || frozen) return;
    polling = true;
    a.removeAttribute('src');
    pollOnce();
  }

  function detach() {
    clearTimeout(timer);
    clearTimeout(guard);
    a.onerror = null;
    a.onload = null;
    hidden.onload = null;
    hidden.onerror = null;
    a.removeAttribute('src');
    b.removeAttribute('src');
  }

  function start() {
    if (!stopped && !frozen && (a.getAttribute('src') || polling)) return;
    stopped = false;
    frozen = false;
    polling = false;
    still.classList.add('hidden');
    still.removeAttribute('src');
    a.classList.remove('hidden');
    b.classList.add('hidden');
    shown = a;
    hidden = b;
    a.onerror = () => { if (!stopped && !frozen) startPolling(); };
    a.src = streamUrl;
    // Поток не отдал ни одной картинки за 6 секунд — значит браузер его не понял.
    guard = setTimeout(() => {
      if (!stopped && !frozen && !polling && !a.naturalWidth) startPolling();
    }, 6000);
  }

  function stop() {
    stopped = true;
    polling = false;
    detach();
  }

  function freeze() {
    if (frozen) return;
    // Снимаем то, что видно сейчас, на холст и показываем как обычную картинку.
    try {
      if (shown.naturalWidth) {
        const canvas = document.createElement('canvas');
        canvas.width = shown.naturalWidth;
        canvas.height = shown.naturalHeight;
        canvas.getContext('2d').drawImage(shown, 0, 0);
        still.src = canvas.toDataURL('image/jpeg', 0.9);
        still.classList.remove('hidden');
      }
    } catch (e) {
      // Не получилось снять кадр — не страшно, картинка просто останется живой.
    }
    frozen = true;
    polling = false;
    detach();
  }

  function thaw() {
    if (!frozen) return;
    frozen = false;
    start();
  }

  start();
  return { start, stop, freeze, thaw, startPolling };
}
