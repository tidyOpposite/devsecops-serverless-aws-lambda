# Етап 1: Builder - завантаження та розпакування FFmpeg
FROM public.ecr.aws/lambda/python:3.9 AS builder

# Встановлюємо необхідні утиліти ТІЛЬКИ на цьому етапі
RUN yum update -y && \
    yum install -y curl tar xz && \
    yum clean all && \
    rm -rf /var/cache/yum

# Завантажуємо статичну збірку ffmpeg
RUN curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg-release-static.tar.xz

# Розпаковуємо архів у тимчасову папку
RUN mkdir -p /opt/ffmpeg && \
    tar -xf /tmp/ffmpeg-release-static.tar.xz -C /opt/ffmpeg --strip-components=1 && \
    rm /tmp/ffmpeg-release-static.tar.xz

# --- Етап 2: Фінальний образ ---
FROM public.ecr.aws/lambda/python:3.9

# Встановлюємо робочу директорію
WORKDIR ${LAMBDA_TASK_ROOT}

# Копіюємо ТІЛЬКИ необхідні бінарні файли з етапу builder
COPY --from=builder /opt/ffmpeg/ffmpeg /usr/local/bin/ffmpeg
COPY --from=builder /opt/ffmpeg/ffprobe /usr/local/bin/ffprobe

# Встановлюємо права на виконання (хоча вони мають зберегтися з builder)
RUN chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe

# Перевірка встановлення ffmpeg (опціонально, але корисно)
RUN ffmpeg -version

# Копіюємо файл з залежностями Python з папки lambda_function/
COPY lambda_function/requirements.txt .

# Встановлюємо залежності Python
RUN pip install --upgrade pip && \
    pip install -r requirements.txt --no-cache-dir

# Копіюємо код нашої Lambda функції з папки lambda_function/
COPY lambda_function/lambda_function.py .

# Встановлюємо команду за замовчуванням для запуску обробника Lambda
CMD [ "lambda_function.lambda_handler" ]
