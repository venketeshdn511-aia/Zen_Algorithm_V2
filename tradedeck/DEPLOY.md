# Deploying TradeDeck v2 to Render

Follow these steps to deploy your trading bot to Render Cloud with persistent MongoDB storage.

## 1. Prerequisites
- A GitHub repository containing the latest code.
- A **MongoDB Atlas** cluster (Free Tier works). Get your `MONGO_URI`.
- Your **Fyers API** Credentials.
- Your **Telegram Bot Token** and **Chat ID**.

## 2. Deployment Steps
1.  Log in to [Render](https://dashboard.render.com).
2.  Click **New +** and select **Blueprint**.
3.  Connect your GitHub repository.
4.  Render will automatically detect the `render.yaml` file.
5.  It will ask you to fill in the **Environment Variable Group** (`tradedeck-secrets`):
    - `FYERS_APP_ID`: Your Fyers App ID.
    - `FYERS_SECRET_ID`: Your Fyers Secret ID.
    - `FYERS_REDIRECT_URI`: Your Fyers Redirect URI.
    - `FYERS_ACCESS_TOKEN`: Your current Access Token.
    - `MONGO_URI`: Your MongoDB Atlas connection string.
    - `TELEGRAM_BOT_TOKEN`: From @BotFather.
    - `TELEGRAM_CHAT_ID`: Your Telegram Chat ID.
6.  Click **Approve** to create the resources.

## 3. Post-Deployment
- **Persistent Storage**: Render will automatically create a 1GB Disk mounted at `/app/reports`. This ensures your PDF audits are saved even after restarts.
- **Monitoring**: Check the service logs to see the startup message: "Rocket *TradeDeck v2 Production* components initialized..."
- **Live Commands**: Once online, try `/start` in your Telegram bot to confirm connectivity.

## 4. Database Notes
The bot is now configured to use **MongoDB** as its primary persistent store for trade logs and system events. If you provide a `DATABASE_URL` (Postgres), it will still use it for real-time strategy state management; otherwise, it will default to a local SQLite database on the persistent disk.
