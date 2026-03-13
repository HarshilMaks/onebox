.PHONY: clean-pycache

clean:
	find . -type d -name '__pycache__' -exec rm -r {} +

install:
	pip install -r requirements.txt

run:
	uvicorn server.main:app --reload --log-config server/logging.ini --host 0.0.0.0 --port 8000

