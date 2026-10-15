# Группы, опекуны и сверка статистики — 14 октября 2026

## Результат

Добавлены группы с созданием, редактированием, активацией/деактивацией и просмотром детей; обязательная принадлежность ребёнка группе; несколько отдельных контактов родителей/опекунов, основной контакт, удаление строки перед сохранением и отображение всех контактов в карточке. Все новые тексты переведены на RU / UZ / ЎЗ. Сохранены существующие стили, авторизация, CSRF, фото, векторы и правила посещений.

Приложение запущено на http://127.0.0.1:5001. Схема рабочей PostgreSQL обновлена; `flask --app app db check` не обнаруживает расхождений.

## Причина нулевых показателей и состояние данных

До изменений выполнена прямая сверка таблиц рабочей PostgreSQL:

- `attendance`: **0 записей**, поэтому количество посещений и суммы действительно равны нулю.
- `scans`: **8 событий** — 6 `recognized`, 1 `not_found`, 1 `low_confidence`.
- События `recognized` с ID **2–7** относятся к детям **1 и 2**, локальная дата **13.10.2026**, время **09:08:45–09:39:05 Asia/Tashkent**.
- Ни одно из этих шести событий не имеет посещения за соответствующий день.

Причина расхождения — старые события распознавания без записи посещения. Список последних сканов отображает `scans`, а финансовые показатели правильно читают `attendance`. Это не потеря суммы при отображении и не ошибка UTC, границ диапазона или статуса ребёнка.

В текущем исходном коде успешный скан **уже** вызывает `services.attendance.register`, который сохраняет посещение со снимком тарифа и событие `success` одной транзакцией. Этот работающий путь не переписан. Исторический статус `recognized` соответствует распознаванию без такого подтверждения. По данным нельзя установить, какой процесс/версия создали старые события; их времени недостаточно, чтобы доказать посещение или исторический тариф.

Добавлены read-only сверка `services/attendance_review.py`, команда `check-attendance`, предупреждение в дашборде/статистике и регрессия: старое `recognized` внутри 15 минут не блокирует первое посещение. Карточка «Сегодня пришли» явно использует количество уникальных детей.

**История не дополнена искусственно.** После миграции в рабочей базе остаются 0 посещений и исходные 8 сканов. Для восстановления старых начислений нужны подтверждение посещения и надёжный исторический тариф; текущий тариф не служит таким доказательством. Шесть распознаваний также нельзя трактовать как шесть посещений: правило допускает максимум одно на ребёнка в день.

## Схема и миграция

Миграция `migrations/versions/b21f8d907c63_groups_and_guardians.py`, предшественник `a047eced711a`, применена.

- `groups`: `id`, `name VARCHAR(120)`, `description VARCHAR(1000)`, `is_active`, `created_at TIMESTAMPTZ`; индексы статуса/времени, непустое имя.
- `children.group_id`: обязательный внешний ключ на `groups.id`, индекс, `ON DELETE RESTRICT`.
- `guardians`: `id`, `child_id` FK, `relation_type`, `first_name`, `last_name`, `middle_name`, `phone`, `is_primary`, `created_at TIMESTAMPTZ`; индексы ребёнка/времени; CHECK допустимых типов родства.
- Частичный уникальный индекс `uq_guardians_primary_child ON guardians(child_id) WHERE is_primary`: не более одного основного контакта.
- `children.parent_first_name`, `parent_last_name`, `parent_middle_name`, `parent_phone` перенесены и удалены. Старые неполные значения сохраняются; родство без доказательств не назначается.
- При наличии старых детей создаётся группа «Без группы (перенесено)». В рабочей базе: 2 ребёнка, 1 группа, 2 контакта; администратору нужно назначить фактические группы.
- Ограничение `UNIQUE(child_id, attendance_date)` и `attendance.amount NUMERIC(14,2)` сохранены.

Перед миграцией создан защищённый дамп `instance/backups/before-groups-20261013T190256Z.dump` с правами 0600. После миграции проверены точные значения старых родительских полей, контрольные суммы строк `photos`, `attendance`, `scans`, `unknown_faces` и файлов фото: сохранность подтверждена.

## Точная логика расчётов

Сегодня определяется как `datetime.now(ZoneInfo(TIMEZONE)).date()`, фактическая настройка — `Asia/Tashkent`. Посещение получает эту бизнес-дату после блокировки строки ребёнка. `created_at` — timezone-aware; PostgreSQL TIMESTAMPTZ хранит момент времени, независимо от отображаемого часового пояса.

Запрос итогов (SQL-эквивалент параметризованного SQLAlchemy в `services/statistics.py`):

```sql
SELECT COUNT(id) AS count,
       COUNT(DISTINCT child_id) AS unique,
       COALESCE(SUM(amount), 0.00) AS amount
FROM attendance
WHERE attendance_date >= :start_date
  AND attendance_date <= :end_date;
```

Для карточек «сегодня» вместо диапазона применяется `attendance_date = :today`. «Сегодня пришли» берёт `unique`; «Всего посещений» — `count`; обе суммы — `amount`. Общее число зарегистрированных детей: `SELECT COUNT(id) FROM children`. Статус ребёнка не исключает исторические посещения.

```sql
SELECT attendance_date, COUNT(id), SUM(amount)
FROM attendance
WHERE attendance_date BETWEEN :start_date AND :end_date
GROUP BY attendance_date;
```

Обе даты включены. Пропущенные календарные дни заполняются нулями только для отображения диаграммы — без вставок в БД. Среднее: `Decimal(count) / ((end_date - start_date).days + 1)`, округление до двух знаков `ROUND_HALF_UP`.

Неизвестные лица независимы от посещений:

```sql
SELECT COUNT(id)
FROM unknown_faces
WHERE created_at >= :today_midnight_tashkent
  AND created_at < :tomorrow_midnight_tashkent;
```

Проверка пропущенных посещений:

```sql
SELECT s.id, s.child_id, s.status, s.created_at,
       timezone(:timezone, s.created_at)::date AS local_date
FROM scans s
WHERE s.status IN ('recognized', 'success', 'already')
  AND s.child_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM attendance a
    WHERE a.child_id = s.child_id
      AND a.attendance_date = timezone(:timezone, s.created_at)::date
  );
```

Для предупреждения дополнительно применяется выбранный диапазон по `local_date`. Этот запрос ничего не создаёт.

Фильтры: сегодня; вчера; сегодня минус 6/29 дней — сегодня; первое число текущего месяца — сегодня; полный предыдущий месяц; 1 января — сегодня; заданные `start`/`end` включительно. HTML-форма и обработчик используют одинаковые имена параметров. Некорректные даты, обратный диапазон и более 731 дня отклоняются с сообщением.

## Проверки

Полный набор: **99 passed, 68.47 s**, без пропусков. После уточнения подписей родительских полей в аудите: ещё **14 passed, 9.81 s** (аудит, группы, опекуны и статистика). Включает:

- PostgreSQL-миграции с переносом полных, отсутствующих и неполных контактов; сохранение фото, векторов и исторических сумм; сравнение схемы с ORM.
- CRUD групп, фильтрацию детей, запрет новых назначений в неактивную группу, сохранение старых назначений, CSRF/авторизацию и три языка.
- Два опекуна, смену основного, удаление одного/всех, некорректные телефоны/типы/индексы, лимит контактов.
- Точный сценарий: группа → отец и мать → тариф 25 000 → распознавание → одно посещение/25 000 → увеличение четырёх показателей → повтор без изменений → 7 дней/месяц/произвольный период → неизвестное лицо меняет только свой счётчик.
- Старое распознавание внутри cooldown, полночь Ташкента при предыдущей UTC-дате, все восемь периодов, включение конечной даты, неизменяемый тариф, UNIQUE и одновременные сканы.
- Полный Chrome E2E на новом временном PostgreSQL-кластере с настоящими YuNet/SFace: три фото, два родителя, мобильное сканирование, отчёты, языки, статусы, аудит, выход. Тестовая камера подаёт записанный портрет; реальные детские данные не используются.
- Проверку ширин 390/768/1440, включая новые страницы групп и форму с несколькими контактами. Снимки — `/tmp/kindscan-groups-review/`; мобильная форма и десктопные группы просмотрены визуально.
- Ruff F401/F841/F821 и проверку синтаксиса JavaScript.

Физическая камера телефона и качество распознавания конкретных детей не проверялись. Прежнее ограничение пассивного liveness не изменено.

## Изменённые и новые файлы

Приложение:

- `app.py`, `config.py`, `commands.py`
- `database/models.py`
- `migrations/versions/b21f8d907c63_groups_and_guardians.py` (новый)
- `routes/children.py`, `routes/groups.py` (новый)
- `services/audit.py`, `services/statistics.py`
- `services/guardians.py`, `services/attendance_review.py` (новые)
- `templates/base.html`, `templates/components.html`, `templates/children.html`
- `templates/child_form.html`, `templates/child_detail.html`, `templates/audit.html`
- `templates/dashboard.html`, `templates/statistics.html`
- `templates/groups.html`, `templates/group_form.html`, `templates/guardian_fields.html` (новые)
- `static/app.js`, `static/app.css`
- `translations/ru.json`, `translations/uz_lat.json`, `translations/uz_cyr.json`

Тесты:

- `tests/conftest.py`, `tests/test_children.py`, `tests/test_application.py`
- `tests/test_audit_security.py`, `tests/test_end_to_end.py`
- `tests/test_reports.py`, `tests/test_face_registration.py`, `tests/test_migration.py`
- `tests/test_groups_guardians.py` (новый)

Документация: `README.md`, этот `GROUPS_STATISTICS_REPORT.md` (новый).
