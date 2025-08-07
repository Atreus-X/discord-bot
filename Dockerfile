# Use a specific Python base image for stability and smaller size
FROM python:3.10-slim-bullseye

# Set the working directory inside the container
WORKDIR /app

# Disable the Google API client's file-based discovery cache
ENV GOOGLE_API_PYTHON_CLIENT_CACHE_DISCOVERY=false

# Copy the requirements file into the container's /app directory
COPY requirements.txt .

# Install the Python dependencies listed in requirements.txt
# --no-cache-dir reduces the image size by not storing pip's cache
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container's /app directory
COPY . .

# Specify the command to run your bot when the container starts
CMD ["python", "main.py"]