# Чек-лист: SSE (Server-Sent Events)

## 🛠️ Чем проверять

- DevTools → Network → EventStream — смотри все события в реальном времени
- DevTools → Network → offline — имитируй обрыв, смотри onerror
- curl — `curl -N http://localhost:5000/api/status/{task_id}` посмотреть сырой стрим
- Postman — GET /api/status/{id}, долгое соединение

## Техники: State Transition, негатив

### State Transition

- [ ] **Connect:** после POST /api/files → opening SSE connection
- [ ] **log event:** сообщение отображается в лог-панели
- [ ] **progress event:** current/total/eta — данные корректны
- [ ] **done event:** лог: "✅ Обработка завершена", спинсер скрыт
- [ ] **error event:** лог: "❌ ...", спинсер скрыт
- [ ] **cancelled event:** лог: "⛔ Обработка отменена пользователем", спинсер скрыт
- [ ] **__done__:** SSE стрим закрывается
- [ ] **keepalive:** если очередь пуста, каждые 30 сек — `: keepalive\n\n`

### Негативные сценарии

- [ ] **Обрыв соединения:** закрыть SSE (например, через DevTools) во время обработки — onerror срабатывает
- [ ] **Таймаут:** длительная обработка > 30 сек без событий — keepalive не даёт соединению упасть
- [ ] **Двойной connect:** повторный POST /api/files до завершения предыдущего — поведение? (busy state)
- [ ] **Закрытие вкладки:** SSE соединение разрывается, pipeline продолжается? (проверить)

### Формат данных

- [ ] JSON в каждом SSE event — парсится без ошибок
- [ ] Спецсимволы в сообщениях (кавычки, эмодзи) — корректно отображаются
