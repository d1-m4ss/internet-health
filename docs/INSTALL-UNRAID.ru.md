# Установка Internet Health на Unraid

[English version](INSTALL-UNRAID.md) · Любая другая машина: [INSTALL.ru.md](INSTALL.ru.md)

На Unraid Internet Health ставится из шаблона, как любой контейнер, а штатные уведомления Unraid (почта и все агенты) могут получать его
сообщения через небольшой скрипт на хосте. Образ тот же, что и везде, — `ghcr.io/d1-m4ss/internet-health` (теги `latest` и номера версий,
например `1.4.0`); всё настраивается на веб-странице.

> **У веб-страницы нет входа по паролю.** Держите порт в локальной сети.

Переменная в командах ниже (поменяйте под себя):

```sh
D=/mnt/user/appdata/internet-health      # папка на хосте, которая подключается как /data (база, настройки, копии)
```

- [Новая установка](#новая-установка) · [Уведомления](#уведомления) · [Проверка](#проверка) · [Обновление](#обновление) ·
  [Перенос папки данных](#перенос-папки-данных) · [Переход с установки NetProbe + файлы](#переход-с-установки-netprobe--файлы) ·
  [Восстановление из копии](#восстановление-из-копии) · [Откат](#откат) · [Если что-то не так](#если-что-то-не-так)

## Новая установка

В терминале Unraid один раз скачайте шаблон:

```sh
wget -O /boot/config/plugins/dockerMan/templates-user/my-InternetHealth.xml \
  https://raw.githubusercontent.com/d1-m4ss/internet-health/main/docs/unraid/internet-health.xml
```

*Docker → Add Container → Template: **InternetHealth*** и заполните:

| Поле | Значение |
|---|---|
| Web UI port | `8788` (или любой свободный) |
| Data | `/mnt/user/appdata/internet-health` — мост ищет папку здесь (и в `netprobe_database`, как она называлась раньше, если есть только она) |
| Router IP / name | ваш роутер, например `192.168.1.1` / `Router` — он станет первым узлом схемы пути |
| Sites to ping | `fast.com,google.com,youtube.com,1.1.1.1,8.8.8.8` — оставьте **два IP-адреса** рядом с именами: они отличают «упал DNS» от «нет интернета» |
| Time zone / schedule time zone | например `Europe/Moscow` (время ежедневного Speedtest считается по второму) |
| Telegram token / chat ID | необязательно — Telegram можно настроить потом на странице |
| Run as user / group (advanced) | `99` / `100` (nobody / users, стандарт Unraid); `0` — работать от root |

*Apply*, затем откройте `http://<unraid>:8788`. Первый цикл проб появляется примерно через минуту. Язык (русский / английский) и стиль
(Classic / UniFi) — в шапке страницы. Можно и через compose: [INSTALL.ru.md](INSTALL.ru.md#вариант-2-docker-compose).

## Уведомления

Куда отправлять сообщения, выбирается на странице, *Настройки → Интеграции*; каналы независимы (любой, оба или ни одного):

- **Telegram прямо из контейнера** — на хосте ничего ставить не нужно. Создайте бота у @BotFather, добавьте его в свой чат, введите токен и ID чата,
  «Отправить тест», «Сохранить». Подробно: [NOTIFICATIONS.md](NOTIFICATIONS.md#integrations-unraid-and-telegram) (на английском).
- **Unraid** (почта и все агенты из *Settings → Notification Settings*) — нужен небольшой мост на хосте Unraid, потому что штатный `notify` может
  вызвать только хост. Поставьте его один раз:

  ```sh
  V=main        # или тег версии, например v1.4.0
  install -d /boot/config/custom
  wget -O /boot/config/custom/netprobe-unraid-notify.sh https://raw.githubusercontent.com/d1-m4ss/internet-health/$V/src/netprobe-unraid-notify.sh
  wget -O /boot/config/plugins/dynamix/netprobe-notify.cron https://raw.githubusercontent.com/d1-m4ss/internet-health/$V/scripts/netprobe-notify.cron
  update_cron
  crontab -c /etc/cron.d -l | grep netprobe          # запись должна быть в списке
  ```

  (У скрипта и cron-файла остались прежние имена `netprobe`, чтобы существующие установки продолжали работать.) Мосту нужны `curl`, `jq` и `flock`
  (есть в Unraid); он читает `notify-token` и `alert-settings.json` из `$D` (если папка или порт другие, задайте в строке cron `NETPROBE_DATA_DIR`
  или `NETPROBE_NOTIFY_TOKEN_FILE`, `NETPROBE_SETTINGS_FILE`, `NETPROBE_STATE_FILE`, `NETPROBE_API_BASE`). Если выключить интеграцию *Unraid* на
  странице, мост ничего не получает; при повторном включении пропущенное не повторяется.

## Проверка

```sh
curl -s http://<unraid>:8788/api/about                              # {"name":"Internet Health","version":"…"}
curl -s http://<unraid>:8788/api/health                             # {"status":"ok", …}
curl -s -o /dev/null -w '%{http_code}\n' http://<unraid>:8788/api/notifications/pending      # 403 без ключа
ls -l $D/notify-token                                               # -rw------- (600)
cat $D/notify-bridge-state.json                                     # с мостом: health "OK", last_poll меняется каждую минуту
docker logs InternetHealth 2>&1 | grep -i -E 'traceback|extension failed'   # пусто
docker ps --format '{{.Names}} {{.Status}}' | grep -i internethealth          # через несколько минут "(healthy)"
```

Во время установки уведомления не отправляются; нажмите «Отправить тест» в настройках Telegram или выньте интернет-кабель из роутера минимум на
3 минуты, чтобы увидеть настоящее: изменение должно подтвердиться в двух циклах проб подряд (около 50 с каждый), поэтому короткие перерывы
считаются случайными — см. [NOTIFICATIONS.md](NOTIFICATIONS.md). *Turbo-режим* (в порогах) ловит обрывы от ~10 с.

## Обновление

Версии публикуются на [GitHub](https://github.com/d1-m4ss/internet-health/releases) с описанием из [CHANGELOG](../CHANGELOG.md) (на английском);
номер версии — в подвале страницы и в `GET /api/about`.

- **В интерфейсе:** *Docker → Check for updates → Update* (шаблон использует `:latest`). Или закрепите версию: репозиторий
  `ghcr.io/d1-m4ss/internet-health:1.4.0`, и переходите дальше, когда решите.
- **Из командной строки:** `docker pull ghcr.io/d1-m4ss/internet-health:latest`, затем пересоздайте контейнер штатным скриптом Unraid
  `/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/rebuild_container InternetHealth` (он собирает контейнер по шаблону; если контейнер был
  остановлен, запустите его потом).

**С 1.4.0 приложение работает от `99:100` (nobody:users), а не от root.** Первый запуск после обновления отдаёт файлы папки данных этому пользователю
(одна строка `entrypoint: handing files …` в логе); мост на хосте и ваши копии работают как раньше, потому что root читает всё. Чтобы вернуть прежнее
поведение, задайте переменную `PUID` = `0`. Откат на старую версию ничего не требует: root работает с файлами любого владельца.

Обновление не трогает папку данных: новые столбцы базы и файлы создаются при первом использовании, а новая функция остаётся **выключенной**, пока вы
её не включите на странице. Перед большим шагом сделайте копию: *Настройки → История → Создать копию* (см. [Восстановление из копии](#восстановление-из-копии)).
После обновления обновите страницу Ctrl+F5 и выполните [проверку](#проверка). Откат — репозиторий с тегом предыдущей версии.

## Перенос папки данных

Ранние версии предлагали `/mnt/user/appdata/netprobe_database`; она продолжает работать, и мост её находит. Чтобы переименовать её в нынешнюю,
остановите контейнер, перенесите папку, поменяйте путь *Data* в настройках контейнера и запустите его снова:

```sh
docker stop InternetHealth
mv /mnt/user/appdata/netprobe_database /mnt/user/appdata/internet-health
# поменяйте путь Data у контейнера (Docker → InternetHealth → Edit → Apply), это пересоздаст его; затем запустите, если он остановлен
```

Если у моста в строке cron свои пути (`NETPROBE_NOTIFY_TOKEN_FILE`, `NETPROBE_SETTINGS_FILE`, `NETPROBE_STATE_FILE` или `NETPROBE_DATA_DIR` для всей папки),
поменяйте их тоже и выполните `update_cron`; без них мост сам следует за папкой.

## Переход с установки NetProbe + файлы

До 1.0 Internet Health ставился как три файла поверх оригинального образа NetProbe (`bmmbmm01/netprobe`). Этот способ больше не поддерживается;
перейдите на образ, сохранив папку данных, — история и настройки останутся:

1. Копия: `mkdir -p $D/backups/pre-image && cd $D/backups/pre-image`, затем
   `cp /boot/config/plugins/dockerMan/templates-user/my-NetProbe.xml .`, `docker inspect NetProbe > container-inspect.json` и
   `docker stop NetProbe && cp -p $D/netprobe.sqlite $D/alert-settings.json $D/netprobe-prefs.json . ; docker start NetProbe`
   (пропустите файлы, которых нет).
2. Отредактируйте `/boot/config/plugins/dockerMan/templates-user/my-NetProbe.xml`: `<Repository>` — `ghcr.io/d1-m4ss/internet-health:latest`, удалите три
   строки `<Config … Target="/app/…">` (монтирование файлов). (Или *Docker → NetProbe → Edit*, поменяйте *Repository* и уберите три пути, *Apply*.)
   Оставшееся монтирование подменит файл внутри образа старым.
3. Пересоздайте контейнер: `/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/rebuild_container NetProbe` (или *Apply* в интерфейсе), выполните
   [проверку](#проверка). Имя, порт, переменные и `/data` сохраняются; переименовать контейнер можно в интерфейсе.
4. Когда убедитесь, что всё работает, старый образ можно удалить: `docker rmi bmmbmm01/netprobe@sha256:…`.

## Восстановление из копии

Копии делаются на странице (*Настройки → История → Создать копию*) в `$D/db-backups/` (хранятся 5 последних), там же их можно скачать. Копия — полный
файл SQLite. Вернуться к ней:

```sh
docker stop InternetHealth
cp -p $D/netprobe.sqlite $D/netprobe.sqlite.before-restore          # сохранить текущую
rm -f $D/netprobe.sqlite-wal $D/netprobe.sqlite-shm                 # хвосты старого файла нельзя применять к копии
cp -p $D/db-backups/netprobe-<дата>-<время>.sqlite $D/netprobe.sqlite
docker start InternetHealth
```

Копия с другой машины кладётся туда же. В копии вся история и данные Speedtest — обращайтесь с ней как с самой базой (у страницы тоже нет пароля).

## Откат

- **Обновление прошло неудачно:** укажите тег предыдущей версии и пересоздайте контейнер (см. [Обновление](#обновление)); копию базы восстанавливайте только
  при необходимости (см. выше).
- **Отключить только уведомления Unraid:** выключите интеграцию *Unraid* на странице или удалите cron-файл:
  `rm /boot/config/plugins/dynamix/netprobe-notify.cron && update_cron`.

## Если что-то не так

| Признак | Что проверить |
|---|---|
| `404` на `/api/targets/*` или незнакомая страница | контейнер не из образа Internet Health (или его перекрывает оставшееся монтирование файла): `docker inspect <имя> --format '{{.Config.Image}} {{json .HostConfig.Binds}}'` |
| Сообщения не приходят | *Настройки → Интеграции*: включён ли канал? Telegram: «Отправить тест» и строка состояния под ним; Unraid: `grep netprobe-notify /var/log/syslog`, настроены ли агенты, `cat $D/notify-bridge-state.json` |
| «monitoring is not working» от моста | цикл проб остановился или API недоступен с хоста (`curl http://127.0.0.1:8788/api/health`) |
| Ошибки токена у моста | `notify-token` — обычный файл с правами `600`, не короче 32 шестнадцатеричных символов |
| `unhealthy` | `docker logs InternetHealth \| tail`; `GET /api/health` отвечает `503` с последней ошибкой, если цикл проб не сохранялся дольше интервала плюс пять минут |
| После обновления страница не изменилась | Ctrl+F5; проверьте версию в подвале |
