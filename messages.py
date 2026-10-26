MESSAGES = {
    "ru": {
        "choose_language": "Выберите язык / Tilni tanlang:",
        "language_set": "Язык установлен: Русский 🇷🇺",
        "welcome": (
            "Отправьте Excel-файл (.xlsx) со столбцом ИНН — либо просто напишите ИНН (один или несколько) текстом.\n"
            "Бот найдёт данные по каждому ИНН и вернёт готовый файл.\n\n"
            "Используйте /language для смены языка.\n"
            "Используйте /cancel для отмены."
        ),
        "not_excel": "Пожалуйста, отправьте файл в формате .xlsx или .xls.",
        "downloading": "Загрузка файла...",
        "download_failed": "Не удалось загрузить файл: {error}",
        "inn_detected": "Столбец ИНН обнаружен. Начинаю обработку...",
        "ask_column": (
            "Не удалось автоматически определить столбец с ИНН.\n\n"
            "Укажите:\n"
            "- Букву столбца (например: `A`, `B`)\n"
            "- Номер столбца (например: `1`, `3`)\n"
            "- Или точное название заголовка"
        ),
        "column_not_found": (
            "Столбец \"{col}\" не найден в файле.\n"
            "Попробуйте ещё раз: укажите букву (например `A`) или точное название."
        ),
        "processing": "Обработка: {current} / {total} ИНН...",
        "done": "Готово. Обработано {count} ИНН.",
        "error": "Ошибка при обработке: {error}",
        "no_inns": "В указанном столбце не найдено ни одного ИНН.",
        "no_inns_in_text": "В сообщении не найдено ни одного ИНН (ожидаются 9 цифр). Напиши ИНН текстом или пришли Excel-файл.",
        "cancelled": "Отменено.",
        "using_column": "Использую столбец {col}. Начинаю обработку...",
    },
    "uz": {
        "choose_language": "Выберите язык / Tilni tanlang:",
        "language_set": "Til o'rnatildi: O'zbek 🇺🇿",
        "welcome": (
            "INN ustuni bo'lgan Excel fayl (.xlsx) yuboring — yoki INNni (bitta yoki bir nechta) matn bilan yozing.\n"
            "Bot har bir INN bo'yicha ma'lumot topib, tayyor faylni qaytaradi.\n\n"
            "Tilni o'zgartirish uchun /language buyrug'ini ishlating.\n"
            "Bekor qilish uchun /cancel buyrug'ini ishlating."
        ),
        "not_excel": "Iltimos, .xlsx yoki .xls formatidagi fayl yuboring.",
        "downloading": "Fayl yuklanmoqda...",
        "download_failed": "Faylni yuklashda xatolik: {error}",
        "inn_detected": "INN ustuni aniqlandi. Ishlov berilmoqda...",
        "ask_column": (
            "INN ustunini avtomatik aniqlab bo'lmadi.\n\n"
            "Ko'rsating:\n"
            "- Ustun harfi (masalan: `A`, `B`)\n"
            "- Ustun raqami (masalan: `1`, `3`)\n"
            "- Yoki aniq sarlavha nomi"
        ),
        "column_not_found": (
            "\"{col}\" ustuni faylda topilmadi.\n"
            "Qaytadan urining: harf (masalan `A`) yoki aniq nom kiriting."
        ),
        "processing": "Ishlov berilmoqda: {current} / {total} INN...",
        "done": "Tayyor. {count} ta INN qayta ishlandi.",
        "error": "Ishlov berishda xatolik: {error}",
        "no_inns": "Ko'rsatilgan ustunda INN topilmadi.",
        "no_inns_in_text": "Xabarda INN topilmadi (9 raqam kutilmoqda). INN ni matn bilan yozing yoki Excel fayl yuboring.",
        "cancelled": "Bekor qilindi.",
        "using_column": "{col} ustuni ishlatilmoqda. Ishlov boshlanmoqda...",
    },
}


def t(context, key: str, **kwargs) -> str:
    lang = context.user_data.get("lang", "ru")
    text = MESSAGES[lang].get(key, MESSAGES["ru"][key])
    return text.format(**kwargs) if kwargs else text
