# Отдельный Telegram-бот

1. Разверните сайт и отдельный репозиторий `finder-telegram-bot`.
2. Создайте собственного бота через BotFather.
3. Сгенерируйте отдельный общий секрет:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

4. В локальном `.env` сайта задайте JSON в одну строку, подставив созданный секрет:

```dotenv
AURENE_API_KEYS_JSON={"telegram":{"secret":"REPLACE_WITH_GENERATED_SECRET","scopes":["telegram:admins:read","orders:read","orders:admin","orders:write","notifications:read","notifications:write","broadcasts:write"]}}
TELEGRAM_BOT_USERNAME=your_bot_username
```

5. В `.env` отдельного бота задайте тот же секрет, `AURENE_API_KEY_ID=telegram`, собственный `TELEGRAM_BOT_TOKEN` и URL сайта. Для локального запуска используйте `http://127.0.0.1:8000`, вне localhost — HTTPS.
6. Перезапустите оба процесса после изменения конфигурации. Для проверки интеграции нужен настоящий токен вашего бота; unit-тесты используют mock transport и работают без него.

Имена `AURENE_API_*` сохранены как часть существующего контракта. Переименовывать их только с одной стороны нельзя. Telegram-ключу не нужны права `catalog:write`.

Роли и контакты менеджеров настраиваются в панели сайта. Право сотрудника не назначается автоматически по Telegram username. Привязка подтверждается кодом/ссылкой на стороне сайта. Регистрация и подтверждение операций остаются ответственностью Django.

Не добавляйте сгенерированный секрет в README, тесты, сообщения об ошибках или коммиты. `.env.example` содержит только названия переменных, а не рабочие значения.