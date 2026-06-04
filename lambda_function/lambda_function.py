import base64
import os
import subprocess  # Для виклику ffmpeg/ffprobe
import uuid  # Для генерації унікальних імен файлів
import shutil  # Для видалення папок
import logging
import json

import boto3  # AWS SDK для Python

# --- Налаштування Логування ---
# Lambda автоматично надсилає print та логи в CloudWatch Logs
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --- Конфігурація (Отримується з змінних середовища Lambda) ---
# Назви S3 бакетів та інші параметри будуть встановлені Terraform
INPUT_BUCKET = os.environ.get(
    "INPUT_BUCKET_NAME", "your-input-bucket-name"
)  # Бакет для тимчасового зберігання вхідних відео (ЗАРАЗ НЕ ВИКОРИСТОВУЄТЬСЯ)
OUTPUT_BUCKET = os.environ.get(
    "OUTPUT_BUCKET_NAME", "your-output-bucket-name"
)  # Бакет для згенерованих GIF
# Регіон AWS (важливо для генерації presigned URL)
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Шлях до тимчасової папки Lambda (/tmp має обмеження по розміру)
TEMP_FOLDER_BASE = "/tmp"

# Дозволені типи контенту (MIME types) - більш надійно ніж розширення
# Перелік може потребувати уточнення залежно від відео форматів
ALLOWED_CONTENT_TYPES = {
    "video/mp4",
    "video/quicktime",  # .mov
    "video/x-msvideo",  # .avi
    "video/x-matroska",  # .mkv
    "video/webm",
    "video/ogg",
}

# Дозволені типи контенту для завантаження з URL
# (можна зробити більш строгими, якщо потрібно)
# ALLOWED_DOWNLOAD_CONTENT_TYPES = { # ЦЕЙ БЛОК БІЛЬШЕ НЕ ПОТРІБЕН
#     "video/mp4",
#     "video/quicktime",
#     "video/x-msvideo",
#     "video/x-matroska",
#     "video/webm",
#     "video/ogg",
#     # Додамо загальні відео MIME типи, якщо сервер не віддає специфічні
#     "video/mpeg",
#     "application/octet-stream", # Часто використовується як fallback
# }

# Максимальний розмір файлу (в байтах) - синхронізувати з API Gateway/Lambda конфігурацією
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

# --- Ініціалізація клієнтів AWS ---
s3_client = boto3.client("s3", region_name=AWS_REGION)

# --- Допоміжні функції ---


def run_command(command):
    """
    Виконує команду shell та повертає її вивід або кидає виняток.
    Переконуємося, що ffmpeg та ffprobe доступні в середовищі Lambda
    (через Lambda Layer або контейнерний образ).
    """
    logger.info(f"Running command: {' '.join(command)}")
    try:
        # Використовуємо encoding='utf-8' для коректної обробки виводу
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, encoding="utf-8"
        )
        logger.info(f"Command stdout: {result.stdout.strip()}")
        if result.stderr:
            logger.warning(f"Command stderr: {result.stderr.strip()}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {' '.join(e.cmd)}")
        logger.error(f"Return code: {e.returncode}")
        # Логуємо stderr та stdout для діагностики
        logger.error(f"Stderr: {e.stderr.strip() if e.stderr else 'N/A'}")
        logger.error(f"Stdout: {e.stdout.strip() if e.stdout else 'N/A'}")
        # Перекидаємо виняток, щоб сигналізувати про помилку
        raise RuntimeError(f"FFmpeg/FFprobe command failed: {' '.join(e.cmd)}") from e
    except FileNotFoundError:
        logger.error(
            f"Command not found (is ffmpeg/ffprobe installed and in PATH?): {command[0]}"
        )
        raise RuntimeError(f"Required command not found: {command[0]}")
    except Exception as e:
        logger.error(f"An unexpected error occurred while running command: {e}")
        raise  # Перекидаємо інші неочікувані помилки


def process_video_to_gif(input_filepath, unique_id, temp_dir):
    """
    Обробляє відео: вибирає короткі сегменти з різних частин
    і конвертує їх у GIF-прев'ю. Повертає шлях до згенерованого GIF.
    Використовує надану тимчасову директорію.
    """
    output_gif_filename = f"{unique_id}.gif"
    # Зберігаємо GIF також у тимчасовій папці Lambda
    output_gif_path = os.path.join(temp_dir, output_gif_filename)

    try:
        # 1. Отримати тривалість відео (ffprobe)
        logger.info(f"Getting duration for {input_filepath}")
        duration_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            input_filepath,
        ]
        duration_str = run_command(duration_cmd)
        try:
            duration = float(duration_str)
            logger.info(f"Video duration: {duration} seconds")
        except ValueError:
            logger.error(f"Could not parse duration: {duration_str}")
            raise ValueError("Could not determine video duration.")

        if duration <= 0.01:
            raise ValueError("Video duration is too short.")

        # 2. Розрахувати точки початку та тривалість коротких сегментів
        num_segments = 10
        segment_duration = 0.5  # Тривалість кожного МІNІ-сегменту
        # Перевірка, щоб уникнути ділення на нуль або від'ємний інтервал
        if duration < segment_duration * num_segments:
            num_segments = max(
                1, int(duration / segment_duration)
            )  # Зменшуємо кількість сегментів
            logger.warning(
                f"Video too short for {num_segments} segments, adjusting to {num_segments}"
            )
        if num_segments == 0:
            raise ValueError("Video too short to extract any segments.")

        interval = duration / float(num_segments)

        select_parts = []  # Список для частин фільтру 'select'
        total_preview_duration = 0.0

        for i in range(num_segments):
            start_time = interval * i
            # Переконуємося, що не виходимо за межі тривалості
            actual_segment_duration = min(segment_duration, duration - start_time)

            # Ігноруємо дуже короткі сегменти, щоб уникнути помилок ffmpeg
            if actual_segment_duration > 0.05:
                end_time = start_time + actual_segment_duration
                # Форматуємо час з 3 знаками після коми
                select_parts.append(f"between(t,{start_time:.3f},{end_time:.3f})")
                total_preview_duration += actual_segment_duration
            else:
                logger.warning(
                    f"Skipping segment at {start_time:.3f}s, not enough time left or segment too short."
                )

        if not select_parts:
            raise ValueError(
                "No valid video segments could be selected for the preview."
            )

        select_filter_string = "+".join(select_parts)
        logger.info(f"Select filter string: {select_filter_string}")
        logger.info(f"Total estimated preview duration: {total_preview_duration:.2f}s")

        # 3. Конвертувати в GIF з вибором сегментів і коректним скиданням PTS
        fps = 15  # Кадрів в секунду для GIF
        scale_width = 320  # Ширина GIF

        # Використовуємо тимчасову директорію для палітри
        palette_path = os.path.join(temp_dir, "palette.png")

        # Фільтр: вибір сегментів + скидання PTS + fps + масштабування
        filtergraph_base = f"select='{select_filter_string}',setpts=N/({fps}*TB),fps={fps},scale={scale_width}:-1:flags=lanczos"

        # Команда створення палітри
        palette_cmd = [
            "ffmpeg",
            "-v",
            "warning",  # Менше логів
            "-i",
            input_filepath,
            "-vf",
            f"{filtergraph_base},palettegen",  # Застосовуємо фільтр ДО palettegen
            "-y",  # Перезаписувати вихідний файл
            palette_path,
        ]
        logger.info("Generating color palette for selected segments...")
        run_command(palette_cmd)

        # Команда створення GIF з використанням палітри
        gif_cmd = [
            "ffmpeg",
            "-v",
            "warning",
            "-i",
            input_filepath,  # Вхідне відео
            "-i",
            palette_path,  # Вхідна палітра
            "-lavfi",
            f"{filtergraph_base} [x]; [x][1:v] paletteuse",  # Комплексний фільтр
            "-loop",
            "0",  # Зациклити GIF
            "-y",
            output_gif_path,
        ]
        logger.info("Generating GIF from selected segments...")
        run_command(gif_cmd)
        logger.info(f"GIF preview saved locally to {output_gif_path}")

        return output_gif_path  # Повертаємо локальний шлях до GIF

    except Exception as e:
        logger.error(f"Error during video processing: {e}", exc_info=True)
        # Видаляємо потенційно неповний GIF, якщо він існує
        if os.path.exists(output_gif_path):
            try:
                os.remove(output_gif_path)
                logger.info(
                    f"Removed potentially incomplete local GIF: {output_gif_path}"
                )
            except OSError as rm_err:
                logger.error(
                    f"Could not remove incomplete local GIF {output_gif_path}: {rm_err}"
                )
        raise  # Перекидаємо виняток, щоб викликати помилку Lambda


def create_presigned_url(bucket_name, object_name, expiration=3600):
    """
    Генерує тимчасове підписане посилання для завантаження об'єкта з S3.
    """
    try:
        response = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": object_name},
            ExpiresIn=expiration,
        )
        logger.info(f"Generated presigned URL for {bucket_name}/{object_name}")
        return response
    except Exception as e:
        logger.error(
            f"Error generating presigned URL for {bucket_name}/{object_name}: {e}",
            exc_info=True,
        )
        return None  # Повертаємо None у разі помилки


# --- Основний обробник Lambda ---


def lambda_handler(event, context):
    """
    Головний обробник подій AWS Lambda. Отримує URL відео через API Gateway,
    завантажує відео, конвертує в GIF, зберігає результат в S3
    і повертає presigned URL для доступу до GIF.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    # Визначаємо унікальний ID для цього запиту/обробки
    unique_id = str(uuid.uuid4())
    # Створюємо унікальну тимчасову директорію в /tmp
    temp_dir = os.path.join(TEMP_FOLDER_BASE, unique_id)
    input_filepath = None  # Шлях до збереженого вхідного файлу
    output_gif_local_path = None  # Шлях до згенерованого GIF у /tmp

    try:
        # Перевірка методу запиту (очікуємо POST)
        http_method = event.get("httpMethod", "GET") # API Gateway v1 payload
        if not http_method: # Для API Gateway v2 HTTP API payload
            http_method = event.get("requestContext", {}).get("http", {}).get("method")

        if http_method != "POST":
            logger.warning(f"Unsupported HTTP method: {http_method}")
            return {
                "statusCode": 405,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },  # Додаємо CORS заголовок
                "body": json.dumps({"success": False, "error": "Method Not Allowed"}),
            }

        # --- 1. Отримати та валідувати вхідні дані ---
        # Отримуємо Content-Type з заголовків
        headers = event.get("headers", {})
        content_type = None
        for key, value in headers.items():
            if key.lower() == "content-type":
                content_type = value.split(';')[0].strip().lower()
                break

        logger.info(f"Received Content-Type: {content_type}")
        if not content_type:
            raise ValueError("Content-Type header is missing.")

        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"File type '{content_type}' is not allowed.")

        # Отримуємо Base64-кодовані дані з тіла запиту
        base64_video_data = event.get("body")
        if not base64_video_data:
            raise ValueError("Request body is empty after checks.")

        # --- 2. Декодування та збереження відео ---
        logger.info("Decoding Base64 video data...")
        try:
            video_bytes = base64.b64decode(base64_video_data)
        except base64.binascii.Error as e:
            logger.error(f"Invalid Base64 data: {e}")
            raise ValueError("Invalid Base64 data.") from e

        logger.info(f"Decoded video size: {len(video_bytes)} bytes")
        if len(video_bytes) == 0:
            raise ValueError("Video data is empty after decoding.")
        if len(video_bytes) > MAX_FILE_SIZE:
            raise ValueError(f"Video file is too large (decoded size > {MAX_FILE_SIZE // 1024 // 1024} MB)")

        # Створення тимчасової директорії
        os.makedirs(temp_dir, exist_ok=True)
        logger.info(f"Created temporary directory: {temp_dir}")

        # Визначення розширення файлу на основі MIME-типу (опціонально, але корисно для ffmpeg)
        # Проста мапа для поширених типів, можна розширити
        mime_to_ext_map = {
            "video/mp4": ".mp4",
            "video/quicktime": ".mov",
            "video/x-msvideo": ".avi",
            "video/x-matroska": ".mkv",
            "video/webm": ".webm",
            "video/ogg": ".ogv", # .ogg може бути аудіо
        }
        file_extension = mime_to_ext_map.get(content_type, ".tmpvideo") # .tmpvideo як fallback

        input_filename = f"{unique_id}{file_extension}"
        input_filepath = os.path.join(temp_dir, input_filename)

        logger.info(f"Saving decoded video to: {input_filepath}")
        try:
            with open(input_filepath, "wb") as f:
                f.write(video_bytes)
            logger.info("Video saved successfully to temporary file.")
        except IOError as e:
            logger.error(f"Failed to write video to temporary file: {e}")
            raise RuntimeError("Could not save video file for processing.") from e

        # --- 3. Запуск обробки відео ---
        logger.info("Starting video processing...")
        output_gif_local_path = process_video_to_gif(
            input_filepath, unique_id, temp_dir
        )
        logger.info("Video processing finished.")

        # --- 4. Завантаження результату (GIF) в S3 ---
        output_gif_s3_key = (
            f"generated/{unique_id}.gif"
        )
        logger.info(
            f"Uploading {output_gif_local_path} to s3://{OUTPUT_BUCKET}/{output_gif_s3_key}"
        )
        # Блок try/except для завантаження в S3
        try:
            s3_client.upload_file(
                output_gif_local_path,
                OUTPUT_BUCKET,
                output_gif_s3_key,
                ExtraArgs={
                    "ContentType": "image/gif"
                },
            )
            logger.info("GIF successfully uploaded to S3.")
        except Exception as e:
            logger.error(f"Failed to upload GIF to S3: {e}", exc_info=True)
            raise RuntimeError("Could not upload generated GIF to S3.") from e

        # --- 5. Генерація Presigned URL ---
        download_url = create_presigned_url(OUTPUT_BUCKET, output_gif_s3_key)
        if not download_url:
            # Навіть якщо URL не згенерувався, GIF вже в S3.
            # Можна повернути успіх, але без URL, або помилку 500.
            # Обираємо помилку 500, щоб сповістити про проблему.
            logger.error("Failed to generate presigned URL after successful upload.")
            raise RuntimeError("Could not generate download URL for the GIF.")

        # --- 6. Успішна відповідь ---
        logger.info("Processing successful. Returning presigned URL.")
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps(
                {
                    "success": True,
                    "message": "GIF generated successfully!",
                    "download_url": download_url,
                    "gif_filename": f"{unique_id}.gif",
                }
            ),
        }

    # --- Обробка помилок ---
    except ValueError as ve:  # Помилки валідації даних (JSON, URL format, file type, size)
        logger.warning(f"Validation Error: {ve}") # Логуємо як попередження
        return {
            "statusCode": 400,  # Bad Request
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"success": False, "error": str(ve)}),
        }
    except RuntimeError as rte:  # Помилки виконання (Download, FFmpeg, S3 Upload, Presigned URL)
        logger.error(f"Runtime Error: {rte}", exc_info=True) # Логуємо як помилку з трасуванням
        return {
            "statusCode": 500,  # Internal Server Error
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"success": False, "error": "Failed to process video due to an internal server error."}),
        }
    except Exception as e:  # Інші неочікувані помилки
        logger.error(f"Unexpected Error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps(
                {"success": False, "error": "An unexpected internal server error occurred."}
            ),
        }
    finally:
        # --- Очищення ---
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temporary directory: {temp_dir}")
            except OSError as e:
                logger.error(f"Could not remove temporary directory {temp_dir}: {e}")
