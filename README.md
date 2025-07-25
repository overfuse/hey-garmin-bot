# Hey Garmin Bot

A simple Telegram bot that allows users to import workout plans into their Garmin Connect account. Built with Python and Pyrogram, and uses MongoDB for persistent user storage.

## Features

- **User Login Flow**: Users authenticate with their Garmin Connect credentials via `/start`. Credentials are kept in a short-lived in-memory session and immediately discarded after exchanging for a Garmin session token.
- **Workout Import**: Once authorized, any text message or workout plan (e.g., GPX content) sent to the bot is uploaded to the user's Garmin Connect account.
- **Logout**: Users can remove their Garmin authorization with the `/logout` command.
- **Session Management**: Temporary credentials are stored in a TTL cache (`cachetools.TTLCache`) with a 5-minute expiration to avoid persisting raw passwords.
- **Persistent Storage**: User state and Garmin session tokens are stored in MongoDB via Motor (async MongoDB driver).

## Tech Stack

- **Language:** Python 3.11
- **Telegram Framework:** [Pyrogram](https://docs.pyrogram.org/)
- **Async MongoDB:** [Motor](https://motor.readthedocs.io/)
- **In-Memory Cache:** [cachetools](https://cachetools.readthedocs.io/)
- **Containerization:** Docker & Docker Compose

## Repository Structure

```text
├── bot.py             # Main script for the Telegram bot
├── user.py            # CRUD helpers for MongoDB
├── view_users.py      # Utility to inspect users in MongoDB
├── Dockerfile         # Docker image definition
├── docker-compose.yml # Compose file for bot + MongoDB
├── requirements.txt   # Python dependencies
├── .env               # Environment variables (not checked in)
└── mongo_data/        # Local MongoDB data volume
```

## Getting Started

### Prerequisites

- Docker & Docker Compose installed
- Telegram Bot credentials: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`

### Environment Variables

Create a `.env` file in the project root:

```dotenv
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=abcdef1234567890
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
MONGODB_URI=mongodb://mongo:27017
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

## Security Considerations

- **Never store raw passwords** – credentials are only kept in memory and purged after authentication.
- **Environment variables** – do not commit your `.env` or service-account keys to version control. Use volume mounts or secret management in production.
- **Non-root container user** – consider adding a non-root user in your Dockerfile for additional security.

## License

This project is licensed under the MIT License. Feel free to use and modify it for your own purposes!
