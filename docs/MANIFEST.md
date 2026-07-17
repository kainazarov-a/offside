# OFFSIDE · MANIFEST (живой статус проекта)

> Читать первым в каждом новом чате. Здесь — что сделано, что дальше, ключевые решения.
> Истина продукта (идеология/архитектура/дизайн) — в BLUEPRINT.md рядом.

## 🏆 САБМИТЫ — ОБА СДАНЫ 17.07.2026
- Трек №1 Trading Tools & Agents: видео https://youtu.be/GRSPBWnIGNk — принят
- Трек №2 Prediction Markets & Settlement (как OFFSIDE Lens): видео
  https://youtu.be/ljyxUMcJI7w — принят
- Призовой кошелёк: встроенный Earn-кошелёк (HPks…PFpd), вывод победы —
  кнопка Withdraw на личный Phantom (2hLd…GxW, сид на бумаге у собственника)
- Итоги: 29.07.2026. Прод ПЕРЕЕХАЛ С НОУТА НА VPS (17.07 ночью): Hetzner **offside3**
  188.245.205.73 (CX23, Falkenstein, пересоздан с правильным SSH-ключом «totti» +
  cloud-init самосборка в /opt/offside). Две systemd-службы: **offside** (--live)
  и **offside-tunnel** (ngrok, тот же домен из сабмитов). Reboot-тест пройден дважды.
  Ноут свободен. Старый засвеченный ngrok-токен отозван (Reset), сервер на своём
  отдельном ключе offside-vps (тоже засвечен в чате — перевыпустить после 29.07).
  Деплой-конвейер: правки локально → тест → git push → на сервере
  `cd /opt/offside && git pull --ff-only && systemctl restart offside`;
  откат = `git reset --hard HEAD~1` + restart

## 🚀 Спринт до дедлайна (сабмит-дедлайн 19.07 23:59 UTC ещё НЕ прошёл — до него улучшать прод легитимно, после — фриз)
- Р1 ✓ в файлах (проверено смоуками на стенде): Replay v3 — стоп (кнопка ■ +
  /api/replay/stop), приватность по зрителю (cid + myReplays: чужие R-карточки/события
  браузер не рисует вообще), свой реплей закреплён ПЕРВЫМ на Live (cool-стиль, чип
  «only you see this»), статус-строка #rpStatus на вкладке (поллер 2.5с),
  надёжный старт (все запросы реплея через verify._get с 401-retry; prewarm пула
  в lifespan; кнопка честно показывает ошибки/409), авто-стоп 20 мин, перезапуск
  тем же cid; engine.remove_match + гард _resolve (реплей не портит acc/Brier);
  ROADMAP-секция на Home (Shipped/Next/Later); TIP-подсказки на метриках;
  docs API += stop/status. Пойманные смоуком баги: CancelledError=BaseException
  (ловим явно) и гонка «finally таски сбрасывает running раньше стопа» — уборка
  карточки сделана безусловной. Бэкапы оригиналов: trash\r1_backup_*.py
- Р1.1 ✓ хотфикс по фидбеку собственника: история одсов реплея ПАРАЛЛЕЛЬНО
  (44 окна, Semaphore 10: ~20-30с → ~3-5с — «лаг» перед стартом ленты убит в корне),
  живой прогресс прямо на закреплённой карточке (rp-prog, EN-статусы: reading
  fixture / downloading score history / downloading odds history / replaying x30),
  тейп без мельтешения (перерисовка только при новых строках, S.tapeV),
  кеш-бастер /app.js?v=3 (браузер больше не покажет старый кешированный фронт),
  match_map.json и *.bat выведены из git (gitignore + git rm --cached: авто-карта
  Polymarket больше не конфликтует с git pull на сервере — первопричина закрыта)
- Р2 (следующий заход): раскрывающиеся карточки Live (клик → детали: крупные
  спарки, сигналы матча, голы), счёт/голы прошлых матчей в списке Replay,
  полоса «турнир к сегодняшнему дню», полиш читабельности
- Гигиена после 29.07: перевыпустить ngrok-токен offside-vps; websockets в requirements

## Цель
Победа в World Cup Hackathon (TxODDS × Solana × Superteam Earn).
Дедлайн сабмита: **19 июля 2026, 23:59 UTC**. Объявление: 29 июля + live-интервью.
Треки: **Trading Tools & Agents** (основной, 10k/4k/2k) → перепаковка в
**Prediction Markets & Settlement** (12k/4k/2k). Один движок — два сабмита.

## Жёсткие требования сабмита (дисквал за нарушение)
- Демо-видео ≤5 мин (Loom/YouTube) — главный фильтр
- Публичный GitHub-репозиторий
- Рабочая ссылка на деплой ИЛИ API-эндпоинт для судей
- Краткая техдока (идея, хайлайты, список использованных эндпоинтов TxLINE)
- Фидбек по TxLINE API
- Мокапы/концепты = автодисквал. TxLINE = обязательный primary data source.

## ✅ Сделано
- Скелет репо, дизайн-токены (чёрный + кислотный лайм), README (EN, база техдоки)
- tools/get_token.mjs — кошелёк+подписка+активация БЕЗ Phantom (devnet/mainnet)
- backend/probe_txline.py — разведка эндпоинтов
- Ядро: engine.py (bus, детектор скачков, lag-метр, бумажный трейдер с латентностью,
  Brier-точность) + sim.py (синтетика: TxLINE мгновенный, толпа отстаёт 2-6с) +
  server.py (FastAPI, SSE, статика)
- Фронт v1: SPA без сборки, вкладки LIVE/AGENT/LAG LAB/PNL/REPLAY, живой тейп
- E2E проверено на симе: сигналы→сделки→lag-записи→PnL. UI подтверждён скрином.
- Devnet SOL получен через faucet (кошелёк DqYEfaHdEJFq8V63xe127Ca8Y63cbHgHkrg9MM7F6sCc)
- MAINNET LEVEL 12 АКТИВИРОВАН (real-time): on-chain subscribe прошёл, токены в .secrets;
  в фиде живые фикстуры ЧМ (comp 72): 1/4 Норвегия–Англия, Аргентина–Швейцария (11.07),
  1/2 Франция–Испания (14.07) + Friendlies (comp 430)
- Схемы подтверждены probe: 1X2 демаржированный (PriceNames part1/draw/part2, Pct=%),
  счёт в Stats["1"]/["2"], GameState-фазы; SSE /odds/stream + /scores/stream живые
- backend/txline.py — боевой адаптер (JWT-автообновление, голы из Stats, kickoff/ft,
  реконнекты, лог неизвестных Action) — юнит-тесты зелёные
- backend/polymarket.py — авто-поиск рынков (Gamma) + CLOB midpoint поллер + ручной
  оверрайд config/match_map.json + режим --probe
- server.py --live: TxLINE + Polymarket в проде; IDL качается автоматом из репо TxODDS
- PAR\HANDOFF.md — скилл передачи контекста новому чату (триггер: «сделай хэндофф»)
- Фронт v2 «ультра»: hero-шапка, canvas-спарклайны tx/pm, lag-гистограмма, countdown,
  вкладка Replay; фикс H1-контаминации (_ft_ok по MarketPeriod + самолог «отсекаю период»)
- PROBE-ФАКТ (закрыл узел №1): у 1X2 ПОЛНОГО матча MarketPeriod = null в снапшоте
  И в стриме (probe_out) → толерантный фильтр его пропускает; live-значение допроверит самолог
- Stats-кодировка подтверждена ОФИЦИАЛЬНОЙ докой (soccer-feed): 1/2 = total goals P1/P2,
  шутаут идёт в 6001/6002 и счёт не искажает; GameState-фазы NS/H1/HT/H2/F/ET/PE/FET/FPE
- Polymarket v2: + WS market channel (best_bid_ask/price_change/book → mid, мс-точность,
  сторож 120с против «молчаливого зависания») + периодический autodiscover каждые 5 мин
  (France–Spain подхватится без рестарта) + REST-поллер как базовая страховка; супервизор
- txline v2: bootstrap-retry (ночь без присмотра), goals_seen.log (fid+Seq гола — сырьё VERIFY)
- replay v2: своя карточка R{fid} (не трогает live-матч), минута из времени ленты,
  окно одсов 220 мин (ОТ+пенальти), home/away по Participant1IsHome
- VERIFY-разведка: GET /api/scores/stat-validation(fixtureId,seq,statKey) → Merkle-пакет;
  on-chain validateStat на PDA daily_scores_roots (дока onchain-validation) — материал для вкладки
- Юнит-тесты зелёные: WS-парсинг/дедуп/мусор, _ft_ok, kickoff→goal, VAR-откат, шутаут, rfid
- ВКЛАДКА VERIFY ГОТОВА (стек целиком): backend/verify.py (кандидаты 6ч..2нед +
  живые голы из goals_seen.log; голы из /scores/historical с Seq; оркестрация) +
  tools/verify_stat.mjs (ЗЕРКАЛО офиц. примера tx-on-chain/subscription_scores_1stat.ts:
  stat-validation → payload → validateStatV2().view() на MAINNET, PDA
  ["daily_scores_roots", u16le(epochDay)], программа 9Exb…cKaA) + 3 роута в server.py +
  вкладка во фронте (бейдж PROVED ON-CHAIN / REJECTED / root pending, Solscan-ссылка).
  Тесты: goals_for (VAR→повторный гол = 3 записи), candidates (живой гол включает матч),
  node --check + резолв импортов, обе ошибочные ветки mjs отдают чистый JSON — всё зелёное
- server.py v0.2: lifespan вместо уходящего on_event (TestClient-smoke зелёный), флаг --host
- README ПЕРЕПИСАН под реальную архитектуру (старый врал про app/ingest/core; бэкап в trash\):
  список эндпоинтов TxLINE (требование техдоки), 6 вкладок, честность, статус-чекбоксы по факту
- deploy/offside.service — systemd-юнит для З4 (Restart=always, --host 0.0.0.0)
- Фронт v3 «ультра+»: фон-поле (виньетка+сетка), hero с градиент-лого и бегущей
  офсайд-линией, KPI-капсулы, ДВОЙНЫЕ БАРЫ sharp/crowd в карточках (отставание видно
  глазом), счёт-капсула gold, пульс live-рамки, шиммер PROVED-бейджа, пустые состояния
  с характером, reduced-motion; бэкап старого index в trash\
- ФИКС пустого Verify/Replay: бесплатный снапшот держит только текущие/будущие фикстуры —
  добавлен почасовой свип /fixtures/updates за 96ч (пачки по 16, кеш 10 мин);
  replay-список и run_replay питаются от общего пула; тесты свипа/кеша зелёные
- ФАКТ по трекам (проверено на Earn): 3 трека = 3 ОТДЕЛЬНЫХ листинга со своим Submit;
  явного запрета мульти-сабмита нет; контакт спонсора t.me/TxLINEChat — вопрос ОТПРАВЛЕН, ждём
- Фронт v5 «ночной стадион»: фон = глубокий нэви (#0C1322) + дрейфующая аврора (80s)
  + звёздное поле-параллакс (160s); словомарка OFFSIDE с офсайд-чертой и мячом
  (навбар+hero, анимация черты); Home += секция «Your first five minutes» (5 шагов
  куда-жать/что-увидишь/зачем с линками на вкладки); Live: подсказка-строка,
  статус-полоса сверху карточек (лайм на live), пульс-точка в пустой ленте
- .gitignore усилен перед публикацией: +trash/ idl/ ngrok.exe *.zip; get_token.mjs проверен —
  хардкодов секретов нет (только публичные program id/mint). GitHub: kainazarov-a
- Деплой-решение: ngrok через Microsoft Store (zip режет Defender — известный ложняк),
  статический бесплатный домен; authtoken ЗАСВЕЧЕН в чате → после запуска туннеля Rotate

## 🔜 Дальше (по порядку, З = заход)
1. З1✓ Ночная готовность (WS-толпа, autodiscover-цикл, retry, replay-фиксы) — сделано
2. З2✓ Код-комплит: VERIFY целиком + lifespan + README v2 + systemd-юнит — сделано;
   собственник: pip install websockets → рестарт --live → клик по Verify → скрин
3. З3 Утро после четвертьфиналов: разбор ночи (сигналы/лаги/PnL/actions_seen/goals_seen),
   фиксы по фактам, replay Норвегия–Англия ×30, VERIFY на ночных голах
4. З4 Деплой Hetzner: /opt/offside + deploy/offside.service + опц. Cloudflare —
   «рабочая ссылка» = требование сабмита
5. З5 GitHub публичный ✓ СДЕЛАНО: https://github.com/kainazarov-a/offside (main, 22 файла,
   без секретов; аккаунт-конфликт OliverGlasses решён через username в remote-URL).
   Фидбек по API + видео ≤5 мин → сабмит трек №1 УТРОМ;
   ПЕРЕД САБМИТОМ: личный Solana-адрес в профиле Superteam Earn (не wallet.json!)
   + ротация засвеченного ngrok-токена.
   Статус пакетов: №1 = 90% (видео+форма), №2 = 85% (текст готов, видео+форма).
   ЖИВОЙ ПРУФ VERIFY: PROVED ON-CHAIN goals(home)=2, Испания 111' seq 967, epoch 20644.
   Вотчдоги run-server.bat / run-tunnel.bat держат ночь; сервер руками НЕ рестартовать до видео
6. З6 Перепаковка «OFFSIDE Lens» (README+видео: viewer/verify) → сабмит трек №2.
   Бонус-матчи до дедлайна: France–Spain 15.07 01:00 Бишкек, Vietnam–Myanmar 18.07 21:00

⚽ БЛИЖАЙШАЯ НОЧЬ 11→12.07 (Бишкек): 03:00 Норвегия–Англия, 07:00 Аргентина–Швейцария
(kickoff из фида: 21:00/01:00 UTC). Сервер --live НЕ выключать; КОМПУ ЗАПРЕТИТЬ СОН
(powercfg /change standby-timeout-ac 0) — иначе ночь пропадёт молча.

## Ключевые решения (не пересматривать без причины)
- Кошелёк wallet.json — ТЕХНИЧЕСКИЙ, денег на нём нет и не будет. Приз — на личный
  Solana-адрес собственника в профиле Superteam Earn (Phantom/Solflare, сид на бумагу).
- Фронт: vanilla JS, без сборщиков. Бек: Python/FastAPI. Один процесс раздаёт всё.
- Хостинг: VPS (долгоживущие SSE и ингест ≠ Cloudflare Workers). CF только DNS/SSL.
- Сим-режим (--sim) сохраняем навсегда: демо/разработка без внешних зависимостей.
- Paper trading only + дисклеймер в футере (юрисдикции, правила хакатона).

## Открытые вопросы
- Значение MarketPeriod полного матча В ЖИВОМ матче (pre-match = null по probe;
  если in-running сменится — самолог назовёт его, фикс = одна строка в FT_PERIODS)
- Строковые значения GameState в ЖИВОМ стриме (ожидаем h1/ht/h2/…; ночь покажет,
  адаптер толерантен: всё не-PRE/не-FT = live)
- Частота idle-ReadError на scores stream — глянуть в ночных логах (реконнект штатный)
- Минута матча из elapsed дрейфует +15' во 2-м тайме (хаймтайм не вычтен) —
  после ночи можно уточнить по GameState-переходам (не блокер)
