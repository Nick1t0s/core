# Воркер transcribe

Транскрибирует локальные аудиофайлы через faster-whisper. Модель, устройство,
тип вычислений и язык задаются в обязательном YAML-конфиге.
Для автоматического определения языка укажите `language: null`. При первом
использовании модель автоматически скачивается через faster-whisper в стандартный
кеш Hugging Face; последующие запуски используют кешированные файлы.
Также можно указать локальный каталог модели. Если модель ещё не скачана,
для первого запуска нужен доступ к Hugging Face.

Запуск из корня проекта для обработки JSON-пачки балансировщика:

```bash
~/whisper-venv/bin/python src/balancer/workers/transcribe/main.py --config /path/to/config.yaml < batch.json
```

Вход — один JSON-документ на stdin; после записи stdin нужно закрыть:

```json
{"type":"transcribe","tasks":[{"task_id":"1","input":{"path":"/tmp/input.wav","mime":"audio/wav"}}]}
```

На stdout возвращается один документ:

```json
{"results":[{"task_id":"1","payload":{"text":"Распознанный текст"}}]}
```

Модель загружается один раз на пачку. Файлы обрабатываются последовательно.
При ошибке любой задачи процесс завершается с кодом 1, сообщение идёт в stderr,
частичные результаты не выводятся. Пустая транскрипция допустима. Входные файлы
воркер не удаляет и не изменяет.

Для одного файла передаётся JSON-пачка с одной задачей в том же формате.

Собственный YAML-конфиг воркер читает через обязательный `--config /path/to/config.yaml`.
Образец — [config.example.yaml](config.example.yaml). В YAML все четыре поля
обязательны; неизвестные поля запрещены. Конфиг представлен классом `Config`
с методами `from_dict` и `from_yaml`; балансировщик его не импортирует.
Настройки `model`, `device`, `compute_type` и `language` задаются только в YAML;
значений по умолчанию нет. Модель определяется полем `model`. Балансировщик передаёт
тип задачи и входные файлы; название модели в JSON-пачку не входит.
Для CPU в YAML нужно указать `device: cpu` и `compute_type: int8`.

Зависимости перечислены в [requirements.txt](requirements.txt). Декодирование
выполняется через PyAV; отдельная проверка системного `ffmpeg` не нужна
([документация faster-whisper](https://github.com/SYSTRAN/faster-whisper#requirements)).
Для GPU нужны CUDA 12 и cuDNN 9. Если библиотеки установлены внутри venv
и появляется ошибка `libcublas.so.12`, добавьте перед запуском их каталоги
`site-packages/nvidia/cublas/lib`, `site-packages/nvidia/cudnn/lib` и
`site-packages/nvidia/cuda_nvrtc/lib` в `LD_LIBRARY_PATH`.
