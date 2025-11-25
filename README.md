# Download Films Bot

A Telegram bot for searching and downloading movies and TV series from RuTracker and Kinopub, with automatic integration to Synology Download Station.

[![GitHub](https://img.shields.io/badge/GitHub-Repository-blue)](https://github.com/tltroll/rutracker-to-synology)

## Features

### 🔍 Search Capabilities
- **Kinopub Integration**: Search movies and TV series via inline mode with poster previews
- **RuTracker Integration**: Search and download torrents from RuTracker tracker
- **Smart Filtering**: Automatic filtering and prioritization of results by:
  - Resolution (1080p, 2160p/4K)
  - HDR/DV support
  - Quality indicators
  - Content type (movies vs TV series)

### 📥 Download Management
- **Automatic Download**: Downloads torrent files and adds them to Synology Download Station
- **Smart Folder Organization**: Automatically sorts downloads into folders:
  - `/downloads/1080p` - for 1080p movies
  - `/downloads/2160p` - for 4K/UHD movies
  - `/downloads/serials` - for TV series (all resolutions)
- **Download Monitoring**: Real-time monitoring of download status with automatic notifications
- **High Priority Downloads**: All downloads are set to high priority in Download Station

### 🎬 Content Support
- **Movies**: Full support with year detection and resolution filtering
- **TV Series**: Special handling for series (year removal from search queries)
- **Metadata Extraction**: Automatic extraction of:
  - Movie/series name
  - Release year
  - Resolution (1080p, 2160p)
  - HDR/DV indicators

### 🔐 Security & Access Control
- **User Access Control**: Restrict bot access to specific Telegram user IDs
- **Secure Configuration**: Environment-based configuration with `.env` file support

### 🚀 Deployment Options
- **Polling Mode**: Default mode for development and simple deployments
- **Webhook Mode**: Production-ready webhook support for scalable deployments
- **Docker Support**: Full Docker containerization with docker-compose

## Requirements

- Python 3.13+
- Synology NAS with Download Station installed
- RuTracker account
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/tltroll/rutracker-to-synology.git
cd rutracker-to-synology
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
pip install py-rutracker-client==0.2.0
```

### 3. Configure Telegram Bot

Before using the bot, you need to enable inline mode in BotFather:

1. Open [@BotFather](https://t.me/BotFather) in Telegram
2. Send `/mybots` command
3. Select your bot
4. Choose "Bot Settings" → "Inline Mode"
5. Enable inline mode

This is required for the inline search feature (Kinopub integration) to work.

### 4. Configure environment variables

Copy `env.example` to `.env` and fill in your credentials:

```bash
cp env.example .env
```

Edit `.env` with your settings:

```env
# Telegram Bot
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
WEBHOOK_URL=  # Optional: for webhook mode

# Rutracker
RUTRACKER_LOGIN=your_rutracker_login
RUTRACKER_PASSWORD=your_rutracker_password
RUTRACKER_PROXY=  # Optional: http://proxy_ip:port
RUTRACKER_USER_AGENT=  # Optional: custom User-Agent

# Synology NAS
SYNOLOGY_HOST=192.168.1.100
SYNOLOGY_PORT=5000
SYNOLOGY_USERNAME=your_synology_username
SYNOLOGY_PASSWORD=your_synology_password
SYNOLOGY_USE_HTTPS=False

# Download Station folders
DOWNLOAD_STATION_FOLDER_1080=/downloads/1080p
DOWNLOAD_STATION_FOLDER_2160=/downloads/2160p
DOWNLOAD_STATION_FOLDER_SERIAL=/downloads/serials

# Allowed users (comma-separated Telegram user IDs)
ALLOWED_USER_IDS=123456789,987654321
```

### 5. Run the bot

```bash
python bot.py
```

## Docker Deployment

### Using Docker Compose

1. Edit `docker-compose.yml` and set your environment variables

2. Build and run:

```bash
docker-compose up -d
```

### Using Docker directly

```bash
docker build -t download-films-bot .
docker run -d --env-file .env download-films-bot
```

## Usage

### Inline Search (Kinopub)

1. Start a chat with the bot
2. Type `@your_bot_name` followed by a movie/series name
3. Select from the results with posters
4. The bot will automatically search RuTracker and show torrent options

### Direct Search (RuTracker)

1. Simply type a movie name in the chat
2. The bot will automatically search RuTracker and show filtered results
3. Select a torrent to view details
4. Click "Download" to start the download

### Commands

- `/start` - Show welcome message and bot information

## How It Works

1. **Search Flow**:
   - User searches via inline mode (Kinopub) or types movie name directly (RuTracker)
   - Bot searches Kinopub for movie metadata with posters
   - Bot searches RuTracker for torrent files
   - Results are filtered and prioritized by quality

2. **Download Flow**:
   - User selects a torrent
   - Bot downloads the `.torrent` file
   - Bot adds torrent to Synology Download Station
   - Bot monitors download progress
   - User receives notification when download completes

3. **Smart Filtering**:
   - Extracts resolution, year, and quality indicators
   - Prioritizes higher quality releases
   - Filters out low-quality or duplicate results
   - Shows up to 15 best results

## Project Structure

```
rutracker-to-synology/
├── bot.py                 # Main bot file with handlers
├── config.py              # Configuration management
├── rutracker_client.py    # RuTracker API client
├── kinopub_client.py      # Kinopub API client
├── synology_client.py     # Synology Download Station client
├── utils.py               # Utility functions (filtering, parsing)
├── patches/               # Patches for synology_api library
│   └── synology_api/
│       └── downloadstation.py
├── docker-compose.yml     # Docker Compose configuration
├── Dockerfile             # Docker image definition
├── requirements.txt       # Python dependencies
├── env.example            # Environment variables template
└── README.md              # This file
```

## Configuration Details

### Required Variables

- `TELEGRAM_BOT_TOKEN` - Your Telegram bot token
- `RUTRACKER_LOGIN` - RuTracker account login
- `RUTRACKER_PASSWORD` - RuTracker account password
- `SYNOLOGY_HOST` - Synology NAS IP address or hostname
- `SYNOLOGY_USERNAME` - Synology NAS username
- `SYNOLOGY_PASSWORD` - Synology NAS password

### Optional Variables

- `WEBHOOK_URL` - Webhook URL for production deployment
- `RUTRACKER_PROXY` - HTTP proxy for RuTracker (if needed)
- `RUTRACKER_USER_AGENT` - Custom User-Agent string
- `ALLOWED_USER_IDS` - Comma-separated list of allowed Telegram user IDs (empty = all users)
- `SYNOLOGY_USE_HTTPS` - Use HTTPS for Synology API (default: False)
- `SYNOLOGY_PORT` - Synology NAS port (default: 5000)

## Features in Detail

### Torrent Filtering

The bot uses intelligent filtering to show only the best quality torrents:

- **Resolution Priority**: Prefers 2160p > 1080p
- **HDR/DV Support**: Identifies and marks HDR/Dolby Vision releases
- **Quality Indicators**: Filters by common quality markers
- **Content Matching**: Ensures torrent matches the searched content

### Download Monitoring

- Checks download status every minute
- Sends completion notification when download finishes
- Handles errors and notifies user
- Automatically stops monitoring when task completes or fails

### Access Control

If `ALLOWED_USER_IDS` is set, only specified users can interact with the bot. Other users receive an access denied message.

## Troubleshooting

### Bot not responding

- Check that `TELEGRAM_BOT_TOKEN` is correct
- Verify bot is running (check logs)
- For webhook mode, ensure `WEBHOOK_URL` is accessible

### Downloads not starting

- Verify Synology NAS credentials
- Check that Download Station is running
- Ensure download folders exist on NAS
- Check network connectivity to NAS

### Search not working

- Verify RuTracker credentials
- Check if proxy is needed (some regions require proxy)
- Ensure internet connectivity

## License

This project is open source and available under the MIT License.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Support

If you encounter any issues or have questions, please open an issue on [GitHub](https://github.com/tltroll/rutracker-to-synology/issues).

