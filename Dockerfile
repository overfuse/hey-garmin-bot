# 1. Pick a small, supported Python base
FROM python:3.11-slim

# 2. Set a working directory
WORKDIR /app

# 3. Bring in only what you need for dependency install
#    (this makes use of Docker's layer caching)
COPY requirements.txt ./

# 4. Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 5. Install Playwright's Chromium browser and its OS-level dependencies
#    (fonts, libX11, NSS, etc. required for headless Chrome)
RUN playwright install --with-deps chromium

# 6. Copy your app code
COPY . .

# 7. Define the command to run your bot
CMD ["python", "bot.py"]
