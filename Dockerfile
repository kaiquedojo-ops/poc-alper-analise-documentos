# Usa Python 3.12 base da AWS Lambda (Amazon Linux 2023)
FROM public.ecr.aws/lambda/python:3.12

# Instala dependências de sistema (ffmpeg, libsm6, libxext6 etc.)
RUN dnf update -y && \
    dnf install -y \
        mesa-libGL \
        mesa-libGLU \
        libXrender \
        libXext \
        libSM \
        libX11 \
        glib2 \
        gcc \
        gcc-c++ \
        make

# Copia o requirements primeiro para aproveitar cache
COPY requirements.txt ./

# Instala dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da Lambda
COPY app.py ./

# Define o handler
CMD ["app.lambda_handler"]
