FROM python:3.11-slim

# 安装常用 Skill 脚本依赖
RUN pip install --no-cache-dir \
    python-docx \
    openpyxl \
    python-pptx \
    PyPDF2 \
    pdfkit \
    markdown \
    jinja2 \
    requests

# 创建非 root 用户运行脚本
RUN useradd -m -s /bin/bash skillrunner

# 工作目录
WORKDIR /skill

# 输出目录
RUN mkdir -p /output && chown skillrunner:skillrunner /output

# 切换到非 root 用户
USER skillrunner

# 入口：执行传入的脚本
ENTRYPOINT ["python"]
