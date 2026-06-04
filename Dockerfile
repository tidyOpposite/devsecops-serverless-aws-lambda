ARG LAMBDA_PYTHON_IMAGE=public.ecr.aws/lambda/python:3.9@sha256:6aa6ba1ae1662df3e7400a25d3293bc464c3a907da13370eec7637128c8eb0a3

FROM --platform=linux/amd64 ${LAMBDA_PYTHON_IMAGE} AS ffmpeg-builder

RUN yum update -y && \
    yum install -y curl tar xz && \
    yum clean all && \
    rm -rf /var/cache/yum

RUN curl -fsSL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
    -o /tmp/ffmpeg-release-static.tar.xz && \
    mkdir -p /opt/ffmpeg && \
    tar -xf /tmp/ffmpeg-release-static.tar.xz -C /opt/ffmpeg --strip-components=1 && \
    rm /tmp/ffmpeg-release-static.tar.xz

FROM --platform=linux/amd64 ${LAMBDA_PYTHON_IMAGE} AS python-deps

COPY lambda_function/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir --target /opt/python -r /tmp/requirements.txt

FROM --platform=linux/amd64 ${LAMBDA_PYTHON_IMAGE} AS runtime

WORKDIR ${LAMBDA_TASK_ROOT}

COPY --from=ffmpeg-builder /opt/ffmpeg/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-builder /opt/ffmpeg/ffprobe /usr/local/bin/ffprobe
COPY --from=python-deps /opt/python /opt/python
COPY lambda_function/lambda_function.py .

RUN groupadd --system lambdaapp && \
    useradd --system --uid 10001 --gid lambdaapp --home-dir ${LAMBDA_TASK_ROOT} --shell /sbin/nologin lambdaapp && \
    chmod 0555 /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    chown -R lambdaapp:lambdaapp ${LAMBDA_TASK_ROOT} /opt/python

ENV PYTHONPATH=/opt/python

USER 10001

CMD ["lambda_function.lambda_handler"]
