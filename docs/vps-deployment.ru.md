# JobPilot на собственном VPS

`compose.vps.yaml` запускает PostgreSQL 17 и существующий production-образ
Next.js + FastAPI с обязательной авторизацией. Это отдельный Compose-проект
`jobpilot-vps` с постоянными томами базы и файлов. Neon для этого запуска не нужен.

Новая база создаётся пустой. При первом запуске приложение применяет свои
Alembic-миграции и стандартные начальные данные. Резюме, профиль и история из
старых баз автоматически не импортируются.

## Подготовка

Сначала проверить ресурсы VPS, свободные порты, установленные Docker/Compose,
права пользователя и существующие сайты. Файлы приложения разместить в отдельном
каталоге. Не копировать `.git`, `.venv`, `node_modules`, `.env`, `data/` и личные
файлы в сборочный контекст.

Создать на VPS файл `.env.vps` с правами `600` (он исключён из Git). Обязательные
значения:

```dotenv
POSTGRES_PASSWORD=<случайный hex, например secrets.token_hex(32)>
API_TOKEN=<отдельный случайный hex, минимум 32 символа>
LOGIN_USER=jobpilot
LOGIN_PASSWORD=<отдельный случайный пароль, минимум 16 символов>
```

Пароль PostgreSQL должен быть пригоден для включения в URL; для генерируемого
пароля использовать hex. Для уведомлений добавить существующие параметры
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `RESEND_API_KEY`, `NOTIFICATION_EMAIL`,
`NOTIFICATION_FROM`, если эти интеграции нужны. Секреты в логи не выводить.

Для публичного HTTPS-домена также задать одинаковые `APP_ORIGIN` и
`PUBLIC_APP_URL`, например `https://jobs.example.com`. Для проверки только через
локальный SSH-туннель эти значения можно пока не задавать.

## Запуск

В каталоге проекта на VPS:

```sh
docker compose --env-file .env.vps -f compose.vps.yaml config --quiet
docker compose --env-file .env.vps -f compose.vps.yaml up -d --build
docker compose --env-file .env.vps -f compose.vps.yaml ps
curl --fail http://127.0.0.1:10000/health
```

Приложение доступно только на loopback VPS. Для первого просмотра с компьютера:

```powershell
& C:\Windows\System32\OpenSSH\ssh.exe -i "$env:USERPROFILE\.ssh\id_ed25519_blyadota" -N -L 10000:127.0.0.1:10000 danil@blyadota.dev
```

Открыть `http://localhost:10000`, войти с `LOGIN_USER` / `LOGIN_PASSWORD`.
Для записи через туннель `APP_ORIGIN` должен быть пустым или равным адресу
туннеля; при настроенном `APP_ORIGIN=https://jobs.blyadota.dev` пользоваться
публичным HTTPS-адресом.
Для постоянного внешнего доступа настроить HTTPS reverse proxy на VPS,
передающий запросы на `127.0.0.1:10000` с исходным Host. PostgreSQL и внутренний
API не имеют опубликованных портов.

Проверить, что анонимный `/` и `/api/backend/profile` возвращают 401,
`/health` отвечает успешно, вход работает, резюме загружаются и скачиваются.
Проверку импорта локальных данных выполнять отдельно, если такой импорт выбран.
Для переноса исходную SQLite и каталог `resumes` поместить в защищённый каталог
`migration/` на VPS. До первого запуска app запустить только db и выполнить:

```sh
docker compose --env-file .env.vps -f compose.vps.yaml up -d --wait db
docker compose --env-file .env.vps -f compose.vps.yaml run --rm --no-deps \
  --user 0 --entrypoint python -v "$PWD/migration:/migration:ro" app \
  scripts/migrate_cloud_data.py --from-env \
  --source /migration/jobpilot-local.db --resumes-dir /migration/resumes
```

Перед импортом образ app должен быть собран командой `docker compose --env-file
.env.vps -f compose.vps.yaml build app`. Импорт обновляет временную копию SQLite,
проверяет хэши резюме и число строк в одной транзакции и отказывается писать в
заполненную базу. Исходная база остаётся неизменной. Автоматические отклики
сбрасываются в ручной режим; поиски, оставшиеся в running/queued, становятся idle.

## Существующий nginx в Docker

Для проверенного VPS `blyadota.dev` корневой Docker bridge имеет адрес
`172.17.0.1`. Файл `compose.vps-proxy.yaml` добавляет привязку app к этому
внутреннему адресу на порту 10001, сохраняя доступ на loopback:10000. На других серверах сначала
проверить адрес `docker0` и при необходимости задать `JOBPILOT_PROXY_BIND_IP`.

```sh
docker compose --env-file .env.vps -f compose.vps.yaml \
  -f compose.vps-proxy.yaml up -d --wait
```

Все последующие команды управления этим запуском выполнять с обоими `-f`.
В существующем reverse proxy создать HTTPS-хост `jobs.blyadota.dev` с upstream
`http://172.17.0.1:10001`. Для nginx, работающего прямо на хосте, использовать
`http://127.0.0.1:10000`. Внутри HTTPS server-блока использовать
`docs/jobpilot-nginx-location.conf` вместо прежнего location `/` этого хоста.
В конфиге не следует дублировать location `/`.

У приложения собственная Basic-авторизация; внешнюю Basic-авторизацию nginx
для этого хоста отключить. Задать серверное имя `jobs.blyadota.dev`, выпустить
действующий TLS-сертификат и включить перенаправление HTTP на HTTPS. На момент
проверки 25 ноября 2026 существующий ответ для этого SNI имел просроченный
сертификат — его использовать нельзя.

Проверить nginx командой `nginx -t` внутри его контейнера, затем выполнить
`nginx -s reload` там же. Убедиться, что `https://jobs.blyadota.dev/health`
отвечает HTTP 200 и JSON status=ready без запроса пароля, а `/` без входа
возвращает 401. Проверить вход, загрузку файла резюме и два сохранённых поиска.

## Переключение и резервные копии

Встроенный планировщик на VPS включён. После окончательного перехода отключить
старый GitHub Actions schedule (`JOBPILOT_SCHEDULE_ENABLED=false`) и старый
runtime Render, чтобы они не продолжали работать с Neon. Адаптировать или
отключить workflow проверки Render deployment. Обновить ссылки и интеграции,
привязанные к прежнему PUBLIC_APP_URL.

Neon и его данные сохраняются до отдельного решения об удалении. В этом варианте
нет переноса актуальной истории из Neon.

Для резервной копии базы на VPS, в отдельный защищённый каталог:

```sh
umask 077
docker compose --env-file .env.vps -f compose.vps.yaml exec -T db \
  pg_dump -U jobpilot -d jobpilot -Fc > jobpilot.dump
```

Также сохранять том `artifacts` и защищённую копию `.env.vps`. Проверить
восстановление в отдельной пустой тестовой базе. Для работающего сервиса
настроить регулярное копирование за пределы VPS. Не применять `down -v`:
эта команда удаляет постоянные данные.

## Мобильный помощник

Откройте `/?view=assistant` и добавьте JobPilot на домашний экран через меню браузера.
В личном чате с ботом используйте `/assistant`, перешлите вопрос рекрутера,
голосовое до минуты или фотографию скриншота. В Mini App доступны та же история
и кнопка отправки выбранного резюме себе. В поддерживающих Web Share Target
браузерах JobPilot также принимает текст через системное меню «Поделиться».

Помощник использует Gemma 3 на VPS, Whisper для речи и Tesseract для изображений.
Ему нужны примерно 4 ГБ на диске под веса, дополнительно место для образов.
При первой установке загрузите текстовую модель, затем запустите сервисы:

```sh
docker compose --env-file .env.vps -f compose.vps.yaml --profile setup run --rm assistant-seed
docker compose --env-file .env.vps -f compose.vps.yaml -f compose.vps-proxy.yaml up -d --build
```

Обработка идёт в закрытой сети Docker. Токен бота и доступ к БД принадлежат приложению.
Диктовку и результат OCR в веб-интерфейсе можно исправить до генерации.
История хранится в PostgreSQL семь дней, её можно удалить раньше. Вложения для
распознавания обрабатываются во временной памяти. Сообщения, которые пользователь
переслал в Telegram, имеют отдельную историю в Telegram.
