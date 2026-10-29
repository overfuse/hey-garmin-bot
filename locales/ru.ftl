# Bot copy, Russian (the default language). Mirrors en.ftl message for
# message, variable for variable (tests/test_i18n.py enforces it). Russian
# plural categories: one (1, 21), few (2-4, 22), many (5-20, 11-14).

## Command menu (☰ button), registered on boot

cmd-start = Войти в Garmin Connect
cmd-settings = Настройки тренировок
cmd-language = Сменить язык
cmd-stats = Статистика использования
cmd-logout = Выйти

## /start, login

start-already-logged-in =
    Вы уже вошли! Пришлите мне план тренировки для импорта.
    Чтобы сменить аккаунт, сначала используйте /logout.
start-welcome =
    Добро пожаловать! Для начала введите имя пользователя Garmin Connect.

    🇬🇧 English: /language
ask-password = Отлично! Теперь введите пароль от Garmin Connect.
login-session-expired = Сессия истекла или недействительна. Используйте /start, чтобы войти снова.
login-in-progress = Вхожу в Garmin Connect...
login-success = Вход выполнен! Пришлите мне любой план тренировки (текстом), и я импортирую его в ваш аккаунт Garmin Connect.
login-bad-credentials =
    ❌ Неверный email или пароль Garmin.
    Используйте /start и попробуйте снова.
login-rate-limited = Garmin временно ограничивает количество входов. Подождите несколько минут и снова попробуйте /start.
login-failed = Не удалось войти: { $error }. Используйте /start, чтобы попробовать снова.
not-logged-in = Сначала войдите с помощью /start.

## /logout

logout-done =
    Вы вышли — я удалил сохранённые данные для входа в Garmin.

    Обратите внимание: сессия на стороне Garmin при этом не аннулируется. Если хотите быть уверены, что ей больше никто не воспользуется, смените пароль Garmin.
logout-not-logged-in = Вы не вошли. Используйте /start, чтобы войти.

## /settings, /language

settings-unavailable = Настройки пока недоступны в этой установке бота.
settings-prompt = Настройте структуру тренировок — разминку, заминку и то, как они заканчиваются:
settings-button = ⚙️ Настройки тренировок
language-prompt = Выберите язык бота:
language-saved = Язык бота — русский.

## /stats

stats-unavailable = Статистика использования сейчас недоступна. Попробуйте чуть позже.
stats =
    📊 **Ваше использование:**

    ⏱ **За час:** { $hourly_used }/{ $hourly_limit }
    📅 **За день:** { $daily_used }/{ $daily_limit }
    📆 **За месяц:** { $monthly_used }/{ $monthly_limit }

## Workout upload

processing = Загружаю тренировку в Garmin Connect...
busy-suffix =
    {""}

    ⚠️ Я обрабатываю только одну тренировку за раз. Пока эта не загрузится, новые сообщения игнорируются — отправьте их потом ещё раз.
session-refreshed = Сессия обновлена, повторяю загрузку...
upload-success =
    Тренировка импортирована! 🎉
    { $url }
processing-time =
    {""}

    ⚡ Обработано за { $ms } мс

## Workout failures (one per workout_service.FailureCode)

fail-rate-limited = ⚠️ { $scope ->
    [hourly] Достигнут часовой лимит ({ $cap } в час). Попробуйте через { $wait } { $wait ->
        [one] минуту
        [few] минуты
       *[many] минут
    }.
    [daily] Достигнут дневной лимит ({ $cap } в день). Попробуйте через { $wait } { $wait ->
        [one] час
        [few] часа
       *[many] часов
    }.
   *[monthly] Достигнут месячный лимит ({ $cap } в месяц). Попробуйте через { $wait } { $wait ->
        [one] день
        [few] дня
       *[many] дней
    }.
}
fail-limiter-down = Сейчас не могу обрабатывать тренировки — учёт использования недоступен. Попробуйте через несколько минут.
fail-llm-busy = Сейчас много тренировок в обработке. Отправьте ещё раз чуть позже.
fail-config-error = У меня что-то сломалось. Попробуйте позже.
fail-provider-quota = ⛔ У меня закончились кредиты ИИ, поэтому сейчас я не могу разбирать тренировки. С текстом тренировки всё в порядке — эта попытка не засчитана в ваш лимит. Попробуйте позже.
fail-parse-timeout = Разбор занял слишком много времени. Попробуйте ещё раз.
fail-parse-failed = Не получилось превратить это в тренировку. Опишите интервалы с дистанциями и темпом, например: «2 км разминка, 10x400 м @ 3:45, 2 км заминка».
fail-not-a-workout = Не похоже на тренировку — пропускаю. Это ошибка? /settings
fail-token-unreadable = Сохранённая сессия Garmin не читается. Используйте /logout, затем /start, чтобы войти снова.
fail-auth-expired = Сессия истекла, обновить её не удалось. Используйте /logout, затем /start, чтобы войти снова.
fail-upload-failed = Не удалось импортировать тренировку в Garmin. Попробуйте ещё раз.
