# Hey Garmin Bot

A simple Telegram bot that allows users to import workout plans into their Garmin Connect account. Built with Python and Pyrogram, and uses MongoDB for persistent user storage.

## Demo

Try it live: [HeyGarminBot on Telegram](https://t.me/HeyGarminBot)

## Features

- **User Login Flow**: Users authenticate with their Garmin Connect credentials via `/start`. Credentials are kept in a short-lived in-memory session and immediately discarded after exchanging for a Garmin session token.
- **Workout Import**: Once authorized, any text message or workout plan sent to the bot is uploaded to the user's Garmin Connect account.
- **AI-Powered Workout Generation**: Converts natural language workout descriptions into structured Garmin workouts using OpenAI GPT-4o-mini.
- **Redis-Based Rate Limiting**: Sliding window rate limiting with Redis (falls back to in-memory or disabled for local dev).
- **Workout Logging**: All prompts and results are logged to MongoDB with processing times and error tracking.
- **Usage Statistics**: Users can check their API usage with the `/stats` command.
- **Logout**: Users can remove their Garmin authorization with the `/logout` command.
- **Session Management**: Temporary credentials are stored in a TTL cache with a 5-minute expiration to avoid persisting raw passwords.
- **Persistent Storage**: User state, Garmin session tokens, and workout logs are stored in MongoDB via Motor (async MongoDB driver).

## Tech Stack

- **Language:** Python 3.11
- **Telegram Framework:** [Pyrogram](https://docs.pyrogram.org/)
- **AI Model:** OpenAI GPT-4o-mini
- **Async MongoDB:** [Motor](https://motor.readthedocs.io/)
- **Redis:** Async Redis client for rate limiting
- **In-Memory Cache:** [cachetools](https://cachetools.readthedocs.io/)
- **Containerization:** Docker & Docker Compose

## Repository Structure

```text
├── bot.py              # Main script for the Telegram bot
├── chatgpt.py          # OpenAI integration for workout generation
├── garmin.py           # Garmin Connect API integration
├── garmin_convert.py   # Workout JSON to Garmin format converter
├── rate_limiter.py     # Redis-based rate limiting with fallback
├── workout_log.py      # MongoDB workout logging
├── user.py             # User CRUD helpers for MongoDB
├── session.py          # Temporary session storage
├── workout_schema.json # JSON Schema for workout validation
├── SYSTEM_PROMPT.md    # AI prompt for workout parsing
├── Dockerfile          # Docker image definition
├── docker-compose.yml  # Compose file for bot + MongoDB + Redis
├── requirements.txt    # Python dependencies
├── .env                # Environment variables (not checked in)
└── mongo_data/         # Local MongoDB data volume
```

## Getting Started

### Prerequisites

- Docker & Docker Compose installed
- Telegram Bot credentials: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`

### Environment Variables

Create a `.env` file in the project root:

```dotenv
# Telegram Bot
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=abcdef1234567890
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11

# OpenAI
OPENAI_API_KEY=sk-proj-...

# MongoDB
MONGODB_URI=mongodb://mongo:27017

# Redis (optional - leave empty to disable rate limiting for local dev)
REDIS_URL=redis://redis:6379/0

# Rate Limiting (optional, defaults shown)
RATE_LIMIT_HOURLY=10
RATE_LIMIT_DAILY=50
RATE_LIMIT_MONTHLY=200
```

### Run with Docker Compose

1. Create a host folder for MongoDB data:
   ```bash
   mkdir -p mongo_data
   ```
2. Start the services:
   ```bash
   docker-compose up -d --build
   ```
3. Monitor logs:
   ```bash
   docker-compose logs -f bot
   ```
4. To stop:
   ```bash
   docker-compose down
   ```

### Local Development (without Docker)

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Ensure MongoDB is running locally on `mongodb://localhost:27017`.
3. Run the bot:
   ```bash
   python bot.py
   ```

## Rate Limiting

The bot implements a Redis-based sliding window rate limiting system to protect your OpenAI API key from abuse:

- **Redis Sliding Window**: Uses sorted sets (ZSET) with timestamps for O(log N) performance
- **Graceful Degradation**: Falls back to in-memory rate limiting if Redis is unavailable
- **Local Dev Mode**: Rate limiting is disabled when `REDIS_URL` is not set
- **Configurable Limits**: Set via environment variables (hourly/daily/monthly)
- **User Commands**:
  - `/stats` - Check current usage statistics
  - Users receive clear error messages with time-to-retry when limits are exceeded

### Architecture

- **Production**: Redis ZSET with automatic cleanup and 31-day expiry
- **Fallback**: In-memory list of timestamps (survives Redis failures)
- **Local Dev**: Rate limiting completely disabled for development convenience

## Workout Logging

All workout generation requests are logged to MongoDB:

- **Prompt Storage**: Original user input text
- **Result Tracking**: Generated workout JSON and Garmin workout ID
- **Performance Metrics**: Processing time in milliseconds
- **Error Logging**: Full error details for failed requests
- **User Analytics**: Query history and statistics per user

## Security Considerations

- **Never store raw passwords** – credentials are only kept in memory and purged after authentication.
- **API Key Protection** – rate limiting prevents abuse of OpenAI API quota.
- **Environment variables** – do not commit your `.env` or service-account keys to version control. Use volume mounts or secret management in production.
- **Non-root container user** – consider adding a non-root user in your Dockerfile for additional security.

## License

This project is licensed under the MIT License. Feel free to use and modify it for your own purposes!
