# MicroK3 🚀

A Flask-based web dashboard for monitoring and managing distributed STM32H743VIT6 microcontroller nodes with automatic failover capabilities.

![Version](https://img.shields.io/badge/version-0.1.0-blue)
![Python](https://img.shields.io/badge/python-3.9+-green)
![License](https://img.shields.io/badge/license-Apache%202.0-orange)

## Features

- 📊 **Real-time Monitoring**: Track node health, status, and performance
- 🔄 **Failover Management**: Automatic failure detection and logging
- 🎯 **Task Distribution**: Visualize task allocation across nodes
- 🔒 **Secure API**: Authentication and rate-limiting on write operations
- 📱 **Responsive Dashboard**: Modern web interface for system oversight
- 🔌 **RESTful API**: Programmatic access to system data

## Architecture

```
┌─────────────────────────────────────────────┐
│           Web Dashboard (Flask)             │
│  ┌────────────┐  ┌──────────────────────┐  │
│  │  Frontend  │  │    REST API          │  │
│  │  (HTML/JS) │  │  (Authenticated)     │  │
│  └────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────┘
                    │
         ┌──────────┴──────────┐
         │   Data Persistence  │
         │  (system_data.json) │
         └──────────┬──────────┘
                    │
    ┌───────────────┼───────────────┐
    │               │               │
┌───┴───┐      ┌───┴───┐      ┌───┴───┐
│Node 1 │      │Node 2 │      │Node 3 │
│STM32H7│      │STM32H7│      │STM32H7│
└───────┘      └───────┘      └───────┘
```

## Installation

### Prerequisites

- Python 3.9 or higher
- pip (Python package manager)
- Git

### Quick Start

```bash
# Clone the repository
git clone https://github.com/GiulioMastromartino/microk3.git
cd microk3

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Linux/macOS:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create environment configuration
cp .env.example .env

# Generate a secure secret key
python -c "import secrets; print(f'SECRET_KEY={secrets.token_hex(32)}')" >> .env

# Edit .env and set your admin password
nano .env  # or use your preferred editor

# Run the application
python app.py
```

Visit **http://127.0.0.1:5050** in your browser.

## Configuration

### Environment Variables

Create a `.env` file (copy from `.env.example`):

```bash
# Flask Configuration
FLASK_ENV=development          # development|production
FLASK_DEBUG=True               # True|False
FLASK_HOST=127.0.0.1          # Use 0.0.0.0 to expose externally
FLASK_PORT=5050

# Security (REQUIRED)
SECRET_KEY=your-generated-secret-key-here

# Authentication
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-secure-password

# Logging
LOG_LEVEL=INFO                 # DEBUG|INFO|WARNING|ERROR
LOG_FILE=logs/app.log
```

### Generating a Secure Secret Key

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## API Documentation

### Authentication

Protected endpoints require HTTP Basic Authentication:

```bash
curl -u admin:password http://localhost:5050/api/update_node \
  -H "Content-Type: application/json" \
  -d '{"node_id": 1, "status": "active"}'
```

### Endpoints

#### Public Endpoints (No Auth Required)

| Method | Endpoint | Description | Rate Limit |
|--------|----------|-------------|------------|
| `GET` | `/api/system_status` | System overview | 30/min |
| `GET` | `/api/nodes` | List all nodes | 30/min |
| `GET` | `/api/nodes/<id>` | Get node details | 30/min |
| `GET` | `/api/failures` | List failures | 30/min |
| `GET` | `/api/tasks` | List tasks | 30/min |
| `GET` | `/health` | Health check | Unlimited |

#### Protected Endpoints (Auth Required)

| Method | Endpoint | Description | Rate Limit |
|--------|----------|-------------|------------|
| `POST` | `/api/update_node` | Update node status | 10/min |
| `POST` | `/api/add_failure` | Log failure | 10/min |

### API Examples

#### Get All Nodes

```bash
curl http://localhost:5050/api/nodes
```

Response:
```json
[
  {
    "id": 1,
    "name": "Node 1",
    "status": "active",
    "type": "STM32H743VIT6",
    "ram": "1MB",
    "flash": "2MB",
    "cpu": "480MHz",
    "active_tasks": ["Motor Control", "Sensor Fusion"],
    "health_score": 85,
    "uptime": "12h 34m",
    "network": "CAN + Ethernet"
  }
]
```

#### Update Node Status

```bash
curl -u admin:password -X POST http://localhost:5050/api/update_node \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": 1,
    "status": "standby",
    "health_score": 95
  }'
```

Response:
```json
{
  "success": true,
  "message": "Node 1 updated successfully",
  "updated_fields": ["status", "health_score"],
  "node": { /* updated node data */ }
}
```

#### Log a Failure

```bash
curl -u admin:password -X POST http://localhost:5050/api/add_failure \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": 1,
    "description": "Communication timeout detected",
    "status": "open"
  }'
```

## Development

### Setup Development Environment

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Setup pre-commit hooks (optional)
pre-commit install
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=. --cov-report=html

# View coverage report
open htmlcov/index.html
```

### Code Quality

```bash
# Format code
black .

# Lint code
flake8 .

# Type checking
mypy app.py
```

## Deployment

### Production Checklist

- [ ] Set `FLASK_ENV=production` in `.env`
- [ ] Set `FLASK_DEBUG=False`
- [ ] Generate strong `SECRET_KEY`
- [ ] Change default `ADMIN_PASSWORD`
- [ ] Use HTTPS (set `SESSION_COOKIE_SECURE=True`)
- [ ] Configure firewall rules
- [ ] Set up reverse proxy (nginx/Apache)
- [ ] Use production WSGI server (Gunicorn)
- [ ] Set up log rotation
- [ ] Configure backups for `data/system_data.json`

### Using Gunicorn (Production)

```bash
# Install Gunicorn
pip install gunicorn

# Run with 4 worker processes
gunicorn --bind 0.0.0.0:5050 --workers 4 --timeout 120 app:app
```

### Docker Deployment

```bash
# Build image
docker build -t microk3:latest .

# Run container
docker run -d \
  -p 5050:5050 \
  -e SECRET_KEY=your-secret-key \
  -e ADMIN_PASSWORD=your-password \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  --name microk3 \
  microk3:latest
```

### Docker Compose

```bash
# Start services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## Project Structure

```
microk3/
├── app.py                  # Main application
├── config.py               # Configuration classes
├── requirements.txt        # Production dependencies
├── requirements-dev.txt    # Development dependencies
├── .env.example           # Environment template
├── .gitignore             # Git ignore rules
├── README.md              # This file
│
├── models/                # Data models
│   ├── __init__.py
│   └── node.py           # Node class
│
├── templates/             # HTML templates
│   ├── base.html
│   ├── index.html
│   └── node.html
│
├── static/                # Static assets
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── dashboard.js
│
├── data/                  # Data storage
│   └── system_data.json  # Node & system data
│
├── logs/                  # Application logs
│   └── app.log
│
└── tests/                 # Test suite
    ├── __init__.py
    ├── test_app.py
    └── test_models.py
```

## Troubleshooting

### Issue: "SECRET_KEY environment variable must be set"

**Solution:** Generate and set a secret key in your `.env` file:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Issue: "Permission denied" when accessing logs

**Solution:** Ensure the logs directory is writable:
```bash
mkdir -p logs
chmod 755 logs
```

### Issue: Rate limit exceeded

**Solution:** Wait for the rate limit window to reset, or adjust limits in `config.py` for development.

### Issue: Changes not persisting

**Solution:** Check that the application has write permissions to `data/system_data.json` and that the directory exists.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/GiulioMastromartino/microk3/issues)
- **Documentation**: [Wiki](https://github.com/GiulioMastromartino/microk3/wiki)

## Changelog

### [0.1.0] - 2026-02-13

**Added**
- Complete security overhaul with authentication
- Data persistence with atomic writes
- Rate limiting on all endpoints
- Comprehensive error handling
- Type hints and validation
- Production-ready configuration
- Logging system
- API documentation
- Docker support

**Fixed**
- Hardcoded secret key vulnerability
- Missing input validation
- Data loss on restart
- Debug mode exposure
- Repository hygiene issues

---

**Made with ❤️ for embedded systems**
