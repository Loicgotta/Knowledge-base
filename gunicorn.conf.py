# Gunicorn configuration file
# https://docs.gunicorn.org/en/stable/settings.html

import os

# Bind to PORT environment variable (Render sets this)
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Workers and threads
workers = 2
threads = 4

# Timeout - 120 seconds for Gemini API calls
timeout = 120

# Keep-alive
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Graceful timeout
graceful_timeout = 30
