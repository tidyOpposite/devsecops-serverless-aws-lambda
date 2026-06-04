// Знаходимо елементи DOM
const uploadForm = document.getElementById('upload-form');
const fileInput = document.getElementById('file');
const submitButton = document.getElementById('submit-button');
const statusArea = document.getElementById('status-area');
const spinner = document.getElementById('spinner');
const statusMessage = document.getElementById('status-message');
const downloadLinkContainer = document.getElementById('download-link-container');

// Плейсхолдер для URL API Gateway. CI/CD замінить його на реальний URL.
const API_ENDPOINT = '%%API_GATEWAY_INVOKE_URL%%';

// Додаємо слухача події 'submit' до форми
uploadForm.addEventListener('submit', (event) => {
    event.preventDefault(); // Запобігаємо стандартній відправці форми

    const file = fileInput.files[0]; // Отримуємо вибраний файл

    // --- Перевірки на клієнті ---
    if (!file) {
        showStatus('Будь ласка, спочатку виберіть файл.', true); // Змінено текст
        return;
    }

    // Перевірка розміру файлу (100MB)
    const maxSize = 100 * 1024 * 1024;
    if (file.size > maxSize) {
         showStatus(`Файл занадто великий. Максимальний розмір ${maxSize / 1024 / 1024} MB.`, true); // Змінено текст
        return;
    }

    // Перевірка типу файлу (MIME type) - дублює перевірку на бекенді
    const allowedTypes = ['video/mp4', 'video/quicktime', 'video/x-msvideo', 'video/x-matroska', 'video/webm', 'video/ogg'];
    if (!allowedTypes.includes(file.type)) {
        showStatus(`Тип файлу '${file.type}' не дозволено.`, true); // Змінено текст
        return;
    }

    // --- Початок процесу завантаження ---
    submitButton.disabled = true; // Блокуємо кнопку
    showStatus('Завантаження та обробка відео...', false, true); // Показуємо спіннер і повідомлення // Змінено текст
    downloadLinkContainer.innerHTML = ''; // Очищаємо попереднє посилання

    // Використовуємо FileReader для читання файлу як Base64
    const reader = new FileReader();

    reader.onload = async (e) => {
        // e.target.result містить дані у форматі data:mime/type;base64,.....
        // Нам потрібна тільки частина після 'base64,'
        const base64String = e.target.result.split(',')[1];

        // Перевіряємо, чи отримали base64 рядок
        if (!base64String) {
             showStatus('Не вдалося прочитати файл.', true); // Змінено текст
             submitButton.disabled = false;
             return;
        }

        try {
            // Відправляємо запит на бекенд (API Gateway -> Lambda)
            const response = await fetch(API_ENDPOINT, {
                method: 'POST',
                headers: {
                    // Надсилаємо тип контенту оригінального файлу
                    'Content-Type': file.type
                },
                // Надсилаємо base64 рядок як тіло запиту
                body: base64String,
            });

            // Отримуємо відповідь у форматі JSON
            const result = await response.json();

            // --- Обробка результату ---
            if (response.ok && result.success) {
                // Успіх - показуємо повідомлення та посилання для скачування
                showStatus(result.message || 'GIF успішно згенеровано!', false); // Прибираємо спіннер // Змінено текст
                const downloadLink = document.createElement('a');
                // Використовуємо presigned URL з відповіді Lambda
                downloadLink.href = result.download_url;
                downloadLink.textContent = 'Завантажити GIF Прев\'ю'; // Змінено текст
                // Атрибут download пропонує ім'я файлу при збереженні
                downloadLink.setAttribute('download', result.gif_filename || 'preview.gif');
                downloadLinkContainer.appendChild(downloadLink);
            } else {
                // Помилка на бекенді або невдалий запит
                showStatus(result.error || 'Сталася невідома помилка.', true); // Змінено текст
            }

        } catch (error) {
            // Мережева помилка або інша проблема з fetch
            console.error('Помилка під час fetch:', error); // Змінено текст
            showStatus('Помилка під час з\'єднання з сервером.', true); // Змінено текст
        } finally {
            // --- Завершення процесу (успіх або помилка) ---
            submitButton.disabled = false; // Розблоковуємо кнопку
            // fileInput.value = ''; // Очищаємо поле вибору файлу (опціонально)
        }
    };

    reader.onerror = () => {
        showStatus('Помилка читання файлу.', true); // Змінено текст
        submitButton.disabled = false; // Розблоковуємо кнопку
    };

    // Запускаємо читання файлу як Data URL (містить base64)
    reader.readAsDataURL(file);
});

// Допоміжна функція для відображення статусу
function showStatus(message, isError = false, showSpinner = false) {
    statusArea.style.display = 'block'; // Робимо область статусу видимою
    statusMessage.textContent = message;
    // Встановлюємо клас для кольору тексту повідомлення
    statusMessage.className = isError ? 'error-message' : 'success-message';

    // Показуємо або ховаємо спіннер
    spinner.style.display = showSpinner ? 'block' : 'none';

     // Якщо це помилка, очищуємо контейнер для посилання
     if (isError) {
        downloadLinkContainer.innerHTML = '';
     }
}
