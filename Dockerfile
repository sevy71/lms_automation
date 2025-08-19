# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install Google Chrome and its dependencies
RUN apt-get update && apt-get install -y wget gnupg ca-certificates \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list'
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container at /app
COPY lms_automation/requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gunicorn

# Copy the rest of the application code into the container at /app
COPY . .

# Add the project root to the Python path
ENV PYTHONPATH=/app

# Make port 8080 available to the world outside this container
# Railway will automatically use this port.
EXPOSE 8080

# Define environment variable
ENV PORT 8080

# Run app.py when the container launches
CMD ["gunicorn", "lms_automation.app:app", "--bind", "0.0.0.0:8080", "--log-file", "-"]