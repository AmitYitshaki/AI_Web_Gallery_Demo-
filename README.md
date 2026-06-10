# ImageForge AI

ImageForge AI is a full-stack web app for creating AI-powered image variations. Users can sign in, upload an image, write a prompt, generate a new version, save results to a gallery, and delete images they no longer need.

## Tech Stack

- **Backend:** Python, Flask, Gunicorn
- **Frontend:** HTML, CSS, Vanilla JavaScript
- **Authentication:** Firebase Auth with email/password and Google OAuth
- **Database:** Cloud Firestore
- **AI Generation:** Hugging Face Inference Providers
- **Storage:** Local server file storage in `static/uploads/`
- **Deployment:** Docker, Render-ready configuration

## Key Features

- Secure user login and registration
- AI chat-style image generation flow
- Local image upload handling
- Saved gallery with original images and generated variations
- Delete images and related variations
- Demo fallback when Hugging Face credits are depleted
- Docker support for deployment

## Running Locally

Install dependencies:

```bash
pip install -r requirements.txt
