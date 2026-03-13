#!/bin/bash

# Check if Docker is installed
if ! command -v docker &> /dev/null
then
    echo "Docker is not installed. Please install Docker to proceed."
    exit 1
fi

echo "Docker is installed."

# Pull the latest Redis image
echo "Pulling Redis image..."
docker pull redis:latest

# Check if a Redis container is already running
if docker ps --format '{{.Names}}' | grep -q '^redis$'; then
    echo "Redis container is already running."
else
    # Run Redis on default port 6379
    echo "Starting Redis container..."
    docker run --rm -d --name redis -p 6379:6379 redis
    echo "Redis container started on port 6379."
fi