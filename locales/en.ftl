# Bot copy, English. Every message here must also exist in ru.ftl with the
# same variables (tests/test_i18n.py enforces it). Syntax: projectfluent.org.

## Command menu (☰ button), registered on boot

cmd-start = Log in to Garmin Connect
cmd-settings = Workout settings
cmd-language = Change language
cmd-stats = Usage stats
cmd-logout = Log out

## /start, login

start-already-logged-in =
    You are already logged in! Send me a workout plan to import.
    Use /logout first if you want to switch accounts.
start-welcome = Welcome! To get started, please enter your Garmin Connect username.
ask-password = Great! Now please enter your Garmin Connect password.
login-session-expired = Session expired or invalid. Please use /start to log in again.
login-in-progress = Logging in to Garmin Connect...
login-success = Successfully logged in! Send me any workout plan (text) to import into your Garmin Connect account.
login-bad-credentials =
    ❌ Incorrect Garmin email or password.
    Please use /start and try again.
login-rate-limited = Garmin is temporarily rate limiting logins. Please wait a few minutes and try /start again.
# $error is the exception class and message, untranslated.
login-failed = Login failed: { $error }. Use /start to try again.
not-logged-in = Please use /start to log in first.

## /logout

logout-done =
    You have been logged out — I deleted your stored Garmin credentials.

    Note: this doesn't invalidate the session on Garmin's side. If you want to be certain it can never be used again, change your Garmin password.
logout-not-logged-in = You are not logged in. Use /start to log in.

## /settings, /language

settings-unavailable = Settings are not available on this deployment yet.
settings-prompt = Configure how your workouts are structured — warmup, cooldown, and how they end:
settings-button = ⚙️ Workout settings
language-prompt = Choose the bot's language:
language-saved = Language set to English.

## /stats

stats-unavailable = Usage stats are unavailable right now. Try again shortly.
stats =
    📊 **Your API Usage:**

    ⏱ **Hourly:** { $hourly_used }/{ $hourly_limit }
    📅 **Daily:** { $daily_used }/{ $daily_limit }
    📆 **Monthly:** { $monthly_used }/{ $monthly_limit }

## Workout upload

processing = Uploading your workout to Garmin Connect...
# Appended to `processing` when a message arrives mid-flight.
busy-suffix =
    {""}

    ⚠️ I can only process one workout at a time. I'm ignoring new messages until this one finishes — resend them after.
session-refreshed = Session refreshed, retrying upload...
upload-success =
    Workout successfully imported! 🎉
    { $url }
# Appended to upload-success when prefs["show_processing_time"] is on.
# $ms is milliseconds, pre-formatted English-style ("12,345") in every language.
processing-time =
    {""}

    ⚡ Processed in { $ms } ms

## Workout failures (one per workout_service.FailureCode)

# $scope is hourly | daily | monthly; $wait is in minutes, hours or days to match.
fail-rate-limited = ⚠️ { $scope ->
    [hourly] Hourly limit reached ({ $cap } per hour). Try again in { $wait } { $wait ->
        [one] minute
       *[other] minutes
    }.
    [daily] Daily limit reached ({ $cap } per day). Try again in { $wait } { $wait ->
        [one] hour
       *[other] hours
    }.
   *[monthly] Monthly limit reached ({ $cap } per month). Try again in { $wait } { $wait ->
        [one] day
       *[other] days
    }.
}
fail-limiter-down = Can't process workouts right now — usage tracking is unavailable. Please try again in a few minutes.
fail-llm-busy = I'm handling a lot of workouts right now. Send that again in a moment.
fail-config-error = Something's broken on my side. Please try again later.
fail-provider-quota = ⛔ I've run out of AI credits, so I can't parse workouts right now. Your workout text is fine — this attempt wasn't counted against your quota. Please try again later.
fail-parse-timeout = Parsing timed out. Please try again.
fail-parse-failed = I couldn't turn that into a workout. Try describing the intervals with distances and paces, e.g. '2km warmup, 10x400m @ 3:45, 2km cooldown'.
fail-not-a-workout = Not a workout, ignored. Wrong? /settings
fail-token-unreadable = Your stored Garmin session is unreadable. Use /logout then /start to log in again.
fail-auth-expired = Session expired and refresh failed. Use /logout then /start to re-login.
fail-upload-failed = Failed to import workout into Garmin. Please try again.
