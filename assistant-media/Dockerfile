FROM debian:bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl cmake g++ make && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSL --retry 3 https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/refs/tags/v1.9.6 -o src.tgz && \
    echo "7ab15dc88022d2425ad94dcffd8abbf1b6d7018089b4e48d6b40780738e0b445  src.tgz" | sha256sum -c - && \
    tar -xf src.tgz --strip-components=1 && \
    cmake -S . -B out -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DWHISPER_BUILD_TESTS=OFF && \
    cmake --build out --target whisper-cli -j2
RUN curl -fL --retry 3 https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin -o model.bin && \
    echo "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b  model.bin" | sha256sum -c -

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/out/bin/whisper-cli /usr/local/bin/
COPY --from=build /build/model.bin /opt/whisper-small.bin
COPY server.py /opt/server.py
USER 65534:65534
ENV OMP_THREAD_LIMIT=2
CMD ["python", "/opt/server.py"]
