# Установка

Kizurium рассчитан на **Wayland** и работает с композиторами **Hyprland, Niri, MangoWC и Sway**.

Поддерживаемые способы установки:

- **Arch Linux / Arch-based** — через `./install.sh` или `makepkg`.
- **Nix / NixOS** — через Flakes.
- **Другие дистрибутивы** — вручную, если доступны необходимые Wayland/GTK-зависимости.

> Для большинства пользователей на Arch достаточно выполнить `./install.sh`.

---

## Быстрый старт

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
./install.sh
```

После установки:

```bash
kizurium-translator --toggle
```

Выберите область экрана — Kizurium распознает текст и покажет перевод поверх него.

Проверить окружение:

```bash
kizurium-translator --doctor
```

### Запуск рабочей копии

Для запуска исходников из клона используйте `./run.sh`. Скрипт находит каталог
проекта независимо от текущей папки и выбирает Python в таком порядке:
`.venv`, установленное окружение Kizurium в XDG data, затем системный Python.
Окружения без доступных GTK4, PyGObject, Cairo и gtk4-layer-shell пропускаются.
Можно указать интерпретатор явно:

```bash
KIZURIUM_PYTHON=/path/to/python ./run.sh --doctor
```

Требуется Python 3.11 или новее и уже установленные зависимости проекта; `run.sh`
не скачивает пакеты и не создаёт окружение автоматически.

Если после обновления системы команда запускается из старого Python и сообщает
`No module named gi` в пользовательском режиме, выполните `./install.sh --user --update`. Установщик пересоздаст
несовместимое окружение на Python с системными GTK-привязками. Если приложение
установлено через pacman, можно сразу запустить `/usr/bin/kizurium-translator --toggle`:
старый launcher в `~/.local/bin` может перекрывать установленную системную команду.

---

## Arch Linux

### Установка

Рекомендуемый способ:

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
./install.sh
```

Установщик открывает меню с основными действиями:

- установка;
- удаление;
- полное удаление (`purge`);
- очистка кэша;
- диагностика (`doctor`).

В меню установки можно выбрать дополнительные компоненты с помощью `↑` / `↓`, цифр, `Space` и `Enter`.

По умолчанию установщик собирает `PKGBUILD` обычным пользователем и устанавливает
архив через `sudo pacman -U`. Приложение видно в `pacman -Qi kizurium-translator`
и графических менеджерах пакетов; команда находится в `/usr/bin`, ярлык — в меню
приложений. Пакет включает **RapidOCR**, в том числе распознавание японского текста.
Rust-модуль анализа кадров и чернил также входит в пакет: `makepkg` устанавливает
`rust` и `maturin` как зависимости сборки, затем собирает нативную часть в release.
Старый launcher, созданный этим установщиком в `~/.local/bin`, удаляется после
успешной установки пакета, чтобы не запускалась устаревшая копия.

Обновить пакет из текущего дерева исходников:

```bash
./install.sh --update
```

Для **Meiki**, **PaddleOCR**, CT2-паков и других Python extras используйте
пользовательский режим с отдельным окружением (он не регистрируется в pacman):

```bash
./install.sh --user
./install.sh --user --yes --meiki --offline-packs
./install.sh --user --update
```

Флаги этих компонентов без `--user` завершаются с подсказкой, чтобы установщик
не пропускал запрошенные возможности. `--prefix` также относится к `--user`.
Офлайн CT2-паки занимают примерно **160 МБ**; Bergamot доступен и в Arch-пакете.

В пользовательском режиме Rust-модуль собирается, если доступна команда `cargo`.
Если её нет или сборка не удалась, приложение продолжает работать на Python.
Для сравнения или диагностики можно отключить все нативные ускорения:

```bash
KIZURIUM_NATIVE=0 kizurium-translator --toggle
```

PaddleOCR — необязательный компонент. Установщик сохраняет вывод `uv` в
`~/.local/state/kizurium-translator/paddle-install.log` и показывает причину
ошибки. При Python 3.14 и новее установщик создаёт отдельный worker на Python 3.13,
поскольку для PaddlePaddle может ещё не быть wheel нового ABI. GTK и основное
приложение продолжают работать в системном Python; OCR-кадры передаются
постоянному worker-процессу. Для диагностики смотри лог выше.

Чтобы не останавливать установку вопросами:

```bash
./install.sh --yes
```

### Офлайн-паки

Установить все доступные паки после основной установки:

```bash
kizurium-translator --pack-install all
```

---

## Удаление

Удалить Arch-пакет (пользовательские данные сохраняются):

```bash
sudo pacman -R kizurium-translator
# либо из клона, также очищая старую пользовательскую установку:
./install.sh --uninstall
```

Удалить пользовательскую установку через установленную команду:

```bash
kizurium-translator --uninstall
```

Удаление спросит, нужно ли удалять пользовательские данные.

Полностью удалить конфигурацию, кэш, логи и офлайн-модели:

```bash
kizurium-translator --uninstall --purge
```

Только очистить кэш переводов и логи, оставив приложение установленным:

```bash
./install.sh --clean-cache
```

Те же действия доступны через установленную команду:

```bash
kizurium-translator --clean-cache
```

### Репозиторий после установки можно удалить

`./install.sh` не запускает Kizurium из каталога клона.

Команда устанавливается в `/usr/bin` (Arch-пакет) либо в пользовательское
окружение (`--user`), поэтому после установки репозиторий можно:

- переименовать;
- перенести;
- удалить.

Установленная команда продолжит работать.

Чтобы обновить приложение из нового каталога:

```bash
./install.sh
```

---

## Где находится команда

Arch-пакет устанавливает `/usr/bin/kizurium-translator`, который уже в `PATH`.
В режиме `--user` команда устанавливается в:

```text
~/.local/bin/kizurium-translator
```

Если `kizurium-translator` не находится после установки, запустите новый shell или проверьте путь напрямую:

```bash
~/.local/bin/kizurium-translator --doctor
```

Если `~/.local/bin` не находится в `PATH`, добавьте его в конфигурацию вашего shell.

---

## Установка через `makepkg`

Для пользователей, которым нужен непосредственно Arch-пакет:

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
makepkg -si
```

Удаление установленного пакета:

```bash
sudo pacman -Rns kizurium-translator
```

---

## Nix / NixOS

В репозитории уже есть Flake.

### Проверить окружение

```bash
nix run .# -- --doctor
```

### Установить профиль

```bash
nix profile add .#kizurium-translator
```

### Подключить репозиторий как Flake input

```nix
{
  inputs.kizurium.url = "github:Kizuchann/kizurium-translator";

  # ...
}
```

### NixOS module

Kizurium предоставляет NixOS module:

```nix
{
  inputs.kizurium.url = "github:Kizuchann/kizurium-translator";

  outputs = { nixpkgs, kizurium, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";

      modules = [
        kizurium.nixosModules.default

        {
          programs.kizurium-translator.enable = true;
        }
      ];
    };
  };
}
```

Модуль добавляет необходимые зависимости в `systemPackages`, включая компоненты для:

```text
grim
slurp
wl-clipboard
tesseract
quickshell
gtk4
gtk4-layer-shell
aria2
```

### Использовать другую версию или собственный пакет

Пакет можно переопределить:

```nix
{
  programs.kizurium-translator = {
    enable = true;
    package = pkgs.callPackage ./path/to/package.nix { };
  };
}
```

После установки офлайн-паки можно добавить отдельно:

```bash
kizurium-translator --pack-install all
```

---

## Зависимости

Kizurium использует системные Wayland/GTK-компоненты и Python-зависимости.

Основные зависимости:

```text
Pillow
numpy
pytesseract
requests

python-gobject
python-cairo
gtk4
gtk4-layer-shell

tesseract
tesseract-data-eng
tesseract-data-jpn
tesseract-data-rus

grim
slurp
quickshell
wl-clipboard
aria2
libnotify
```

На Arch рекомендуется использовать `./install.sh`, чтобы не устанавливать их по одному вручную.

---

## Горячие клавиши

Kizurium не навязывает глобальные сочетания клавиш: их удобно назначить средствами самого композитора.

### Hyprland — `hyprland.conf`

```ini
bind = SUPER_SHIFT, T, exec, kizurium-translator --toggle
```

### Hyprland — Lua-конфигурации

```lua
hl.bind(
    "SUPER + SHIFT + T",
    hl.dsp.exec_cmd("kizurium-translator --toggle")
)
```

### Niri

```kdl
Mod+Shift+T {
    spawn "kizurium-translator" "--toggle"
}
```

### MangoWC

```text
bind=SUPER+SHIFT,t,spawn,kizurium-translator --toggle
```

### Sway

```text
bindsym $mod+Shift+t exec kizurium-translator --toggle
```

Чтобы получить подсказку именно для текущего окружения:

```bash
kizurium-translator --doctor
```

---

## Несколько мониторов

Для выбора области Kizurium использует экранный селектор.

При необходимости область можно задать вручную:

```bash
kizurium-translator --region "X,Y WxH"
```

Или выбрать конкретный выход:

```bash
kizurium-translator --output
```

Конкретный синтаксис зависит от текущего режима запуска и конфигурации.

---

## Живой перевод

Запустить Live Translation для области:

```bash
kizurium-translator --live -g 0,0 1920x1080
```

Где:

```text
-g 0,0 1920x1080
│  │   │
│  │   └─ размер области: 1920×1080
│  └───── координата X/Y начала области: 0,0
└──────── геометрия области
```

Для обычного одноразового перевода вместо этого используйте:

```bash
kizurium-translator --toggle
```

---

## Офлайн-режим

Чтобы запретить использование сетевых translation backends:

```bash
kizurium-translator --offline-only
```

Это удобно, когда нужен полностью локальный сценарий работы.

Подробнее о том, какие данные могут покидать систему:

[`trust.md`](trust.md)

---

## Ручная установка на других дистрибутивах

Если для вашего дистрибутива нет готового пакета, установите системные зависимости самостоятельно, затем создайте окружение проекта:

```bash
uv venv --system-site-packages
uv sync --extra rapid --extra local-mt
```

После этого убедитесь, что доступны:

```bash
grim --version
slurp --version
tesseract --version
```

И проверьте Kizurium:

```bash
kizurium-translator --doctor
```

Результат `--doctor` поможет определить, какой компонент отсутствует или не настроен.

---

## Если что-то не работает

### `uv: command not found`

На Arch:

```bash
sudo pacman -S uv
```

Либо разрешите установщику добавить отсутствующий компонент.

### `kizurium-translator: command not found`

Откройте новый shell и проверьте:

```bash
~/.local/bin/kizurium-translator --doctor
```

Если эта команда работает, добавьте `~/.local/bin` в `PATH`.

### `No module named 'gi'`

Повторно запустите установку:

```bash
./install.sh
```

Для ручной установки убедитесь, что установлены `python-gobject` и связанные GTK-зависимости.

### Quickshell не найден

Установите его средствами вашего дистрибутива.

На Arch:

```bash
sudo pacman -S quickshell
```

Для режимов, которым он не требуется, можно использовать ручной `--region` или `--output`.

### Wayland не найден

Kizurium предназначен для Wayland-сессии.

Проверьте окружение:

```bash
echo "$XDG_SESSION_TYPE"
```

Ожидаемое значение:

```text
wayland
```

### Диагностика

При любой неоднозначной проблеме начните с:

```bash
kizurium-translator --doctor
```

---

## Что ставит установщик

`./install.sh` предназначен не только для первого запуска: через него можно управлять установкой и обслуживанием Kizurium.

```text
install
├─ основные пакеты
├─ Meiki
└─ offline packs (опционально)

maintenance
├─ uninstall
├─ purge
└─ clean-cache

diagnostics
└─ doctor
```

Офлайн-паки можно не устанавливать сразу и добавить позже:

```bash
kizurium-translator --pack-install all
```

---

## Дополнительно

Приватность и сетевые запросы:

[`trust.md`](trust.md)

Основной README:

[`../README.md`](../README.md)

Исходный код и Issues:

[GitHub](https://github.com/Kizuchann/kizurium-translator)
