# IELTS Mock Exam Platform
[![Ask DeepWiki](https://devin.ai/assets/askdeepwiki.png)](https://deepwiki.com/ss-diyor/mock-ss.git)

- **Website:** https://ielts.sultanov.space
- **Documentation:** https://ss-diyor.github.io/mock-ss-info/mock-ss-info.pdf

This repository contains the source code for a comprehensive IELTS mock exam platform. It allows users to register, take mock tests for the Listening, Reading, and Writing sections, and receive scores. The platform is built with a FastAPI backend and a vanilla JavaScript frontend.

## Features

-   **User Authentication**: Secure user registration, login, and profile management with JWT-based sessions.
-   **Telegram Integration**: Support for login via Telegram and sending user notifications (e.g., result readiness, verification codes).
-   **Mock Exams**:
    -   **Listening**: Interactive listening test with an integrated audio player.
    -   **Reading**: Split-panel interface for reading passages and answering questions simultaneously.
    -   **Writing**: Separate text areas for Task 1 and Task 2 with real-time word count.
-   **Automated & Manual Scoring**:
    -   Automatic band score calculation for Listening and Reading sections.
    -   Admin interface for manual grading and feedback on Writing submissions.
-   **Admin Dashboard**:
    -   View user and test statistics.
    -   Manage users (suspend, delete).
    -   Review all test results.
    -   Grade writing tasks.
    -   Export user and result data to CSV/Excel.
-   **Email & PDF Notifications**: Automatically sends results to users via email, with a professionally formatted PDF certificate attached.
-   **Referral System**: Users can invite others using a unique referral link and track their referral count.
-   **Profile Management**: Users can update their personal information and upload a custom avatar.

## Tech Stack

-   **Backend**: Python, FastAPI
-   **Database**: PostgreSQL (using `asyncpg`)
-   **Authentication**: PyJWT, bcrypt
-   **Frontend**: HTML, CSS, JavaScript (no framework)
-   **Deployment**: Configured for Render (`render.yaml`)
-   **Email Service**: Resend API
-   **PDF Generation**: FPDF2
-   **Asynchronous HTTP**: HTTPX

## Getting Started

To run this project locally, follow these steps.

### Prerequisites

-   Python 3.8+
-   A running PostgreSQL database instance.

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/ss-diyor/mock-ss.git
    cd mock-ss
    ```

2.  **Install Python dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Set up environment variables:**
    Create a `.env` file in the root directory and populate it with the necessary values. You can use the `render.yaml` file as a reference.

    `.env` file example:
    ```env
    DATABASE_URL="postgresql://user:password@host:port/dbname"
    JWT_SECRET="your-strong-jwt-secret"
    ADMIN_SECRET="your-secure-admin-password"
    RESEND_API_KEY="your-resend-api-key"
    EMAIL_FROM="noreply@yourdomain.com"
    TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
    TELEGRAM_ADMIN_CHAT_ID="your-telegram-chat-id-for-admin-notifications"
    FRONTEND_BASE_URL="http://localhost:8000"
    ```

### Running the Application

Once the environment variables are set, you can start the FastAPI server using Uvicorn:

```bash
uvicorn main:app --reload
```

The application will be available at `http://localhost:8000`.
-   The main page is at `/`.
-   The admin panel is at `/admin`.
-   The user profile page is at `/profile`.

## Project Structure

-   `main.py`: The main FastAPI application file. It defines routes for starting exams, submitting results, admin functionalities, and serving static files.
-   `auth.py`: Handles all user-related logic, including registration, login, profile updates, password reset, and JWT management.
-   `db.py`: Manages the asynchronous database connection pool for PostgreSQL.
-   `telegram.py`: Contains helper functions for sending messages and notifications via the Telegram Bot API.
-   `scoring.py`: Includes the logic to convert raw scores from Listening/Reading tests into IELTS band scores.
-   `static/`: This directory contains all frontend assets, including HTML pages for the tests, admin panel, and user profiles.
-   `requirements.txt`: A list of all Python dependencies required for the project.
-   `render.yaml`: Configuration file for deploying the application on the Render platform.

```
  ├── static/                 # Frontend fayllari 
  │   ├── admin.html          # Administrator boshqaruv paneli 
  │   ├── index.html          # Asosiy sahifa 
  │   ├── profile.html        # Foydalanuvchi shaxsiy kabineti 
  │   ├── reading.html        # Reading testi demo sahifasi 
  │   ├── Listening-demo.html # Listening testi demo versiyasi 
  │   ├── Reading-demo.html   # Reading testi demo versiyasi 
  │   └── writing-demo.html   # Writing testi demo versiyasi 
  ├── auth.py                 # Telegram login va autentifikatsiya 
  ├── db.py                   # Ma’lumotlar bazasi (PostgreSQL) 
  ├── main.py                 # Asosiy FastAPI ilovasi 
  ├── scoring.py              # Test natijalarini hisoblash mantigʻi 
  ├── telegram.py             # Telegram bot integratsiyasi 
  ├── render.yaml             # Deployment konfiguratsiyasi 
  ├── requirements.txt        # Python kutubxonalari 
  └── README.md               # Loyiha haqida ma’lumot 
  ```
 

## Admin Panel

The admin panel is a key feature of the platform, accessible at the `/admin` route. Access is protected by the `ADMIN_SECRET` environment variable.

**Admin capabilities include:**
-   **Viewing Comprehensive Stats**: See an overview of total users, test attempts, daily activity, and band score distributions.
-   **Managing Results**: Filter and view all submitted test results.
-   **Grading Writing**: Review writing submissions, assign a band score, and provide feedback, which is then emailed to the student.
-   **User Management**: Search, view, suspend, or delete user accounts.
-   **Data Export**: Download user data and test results in CSV or Excel format for offline analysis.

                                                   2026-2027 - Diyorbek Sultanov
