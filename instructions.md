# Key Value Copy

A secure key-value store with password-protected access. All values are encrypted using AES-256-GCM via the Web Crypto API.

## Getting Started

1. After installing, go to **Properties** to find your auto-generated password.
2. Visit the service's web interface from your StartOS dashboard.
3. Enter the password to log in.

## Changing the Password

Go to **Config** on the service page in your StartOS dashboard to change the password. The service will restart with the new password. Your stored keys and values are **not affected** by password changes — the password is only used for login authentication, not data encryption.

## Usage

- Add key-value pairs using the **+** button
- Click the **copy icon** to copy a value to your clipboard (auto-clears after 30 seconds)
- Click the **eye icon** to reveal a value (auto-hides after 5 seconds)
- Drag and drop to reorder entries
- Export/import your data as encrypted `.kvc` files with a separate export password

## Notes

- **Data is stored on the server** and accessible from any browser or device.
- Values are encrypted client-side with AES-256-GCM before being sent to the server.
- Backups via StartOS include all stored data and the login password.
- Use the built-in export feature for an additional portable backup.
