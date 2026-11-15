# Воркер transcribe

Транскрибирует локальные аудиофайлы через faster-whisper. По умолчанию использует
`large-v3-turbo`, CUDA и `float16`, язык определяет автоматически. При первом
использовании модель автоматически скачивается через faster-whisper в стандартный
кеш Hugging Face; последующие запуски используют кешированные файлы.
Также можно указать локальный каталог модели. Если модель ещё не скачана,
для первого запуска нужен доступ к Hugging Face.

Запуск из корня проекта для обработки JSON-пачки балансировщика:

```bash
~/whisper-venv/bin/python -m src.balancer.workers.transcribe.main < batch.json
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

Отдельный файл можно распознать с выводом обычного текста:

```bash
~/whisper-venv/bin/python -m src.balancer.workers.transcribe.main --file /tmp/input.wav --language ru
```

Собственный YAML-конфиг воркер читает через `--config /path/to/config.yaml`.
Образец — [config.example.yaml](config.example.yaml). Поля необязательны:
пропущенные получают значения по умолчанию. Конфиг представлен классом `Config`
с методами `from_dict` и `from_yaml`; балансировщик его не импортирует.
Параметры CLI `--model`, `--device`, `--compute-type`, `--language` переопределяют YAML.
Модель выбирает сам воркер: `--model` имеет приоритет над полем `model` в YAML,
затем используется значение по умолчанию `large-v3-turbo`. Балансировщик передаёт
тип задачи и входные файлы; название модели в JSON-пачку не входит.
Для CPU нужно явно выбрать `--device cpu --compute-type int8`.

Зависимости перечислены в [requirements.txt](requirements.txt). Декодирование
выполняется через PyAV; отдельная проверка системного `ffmpeg` не нужна
([документация faster-whisper](https://github.com/SYSTRAN/faster-whisper#requirements)).
Для GPU нужны CUDA 12 и cuDNN 9. Если библиотеки установлены внутри venv
и появляется ошибка `libcublas.so.12`, добавьте перед запуском их каталоги
`site-packages/nvidia/cublas/lib`, `site-packages/nvidia/cudnn/lib` и
`site-packages/nvidia/cuda_nvrtc/lib` в `LD_LIBRARY_PATH`.

Проверки из корня проекта:

```bash
~/whisper-venv/bin/python -B -m unittest discover -s tests -v
```
