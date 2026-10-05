FROM python:3.10-slim

# Environment variable so Python output doesn't buffer and prints to Railway logs instantly
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy requirements and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser and its OS-level dependencies
RUN playwright install chromium --with-deps

# Copy the actual code
COPY bot.py .

# Give permissions so any user can write seen_messages.json
RUN chmod -R 777 /app

# Start the bot
CMD ["python", "bot.py"]
