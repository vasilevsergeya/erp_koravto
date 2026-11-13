import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
void i18n.use(initReactI18next).init({lng:'ru', fallbackLng:'ru', interpolation:{escapeValue:false}, resources:{ru:{translation:{
  brand:'KORAVTO', workspace:'Рабочее пространство', inbox:'Диалоги', boards:'Задачи', customers:'Клиенты', customersHint:'Общая карточка, диалоги и история работы', settings:'Настройки',
  logout:'Выйти', login:'Войти', email:'Email', password:'Пароль', signIn:'Вход в CRM', signInHint:'Клиенты, сообщения и работа команды — в одном месте.',
  authTag:'ДОСТУП ДЛЯ КОМАНДЫ', security:'Защита аккаунта', passwordTitle:'Создайте постоянный пароль', passwordHint:'Не менее 7 символов. Временный пароль после смены перестанет действовать.',
  newPassword:'Новый пароль', savePassword:'Сохранить пароль',
  loading:'Загрузка…', connected:'Система работает', degraded:'Есть неполадки', checking:'Проверяем соединение', version:'CRM / WORKSPACE',
  inboxHint:'Переписка с клиентами и работа команды', noDialogs:'Пока нет диалогов', noDialogsHint:'Подключите Wazzup в настройках. Новые сообщения появятся здесь автоматически.',
  openSettings:'Открыть настройки', chooseDialog:'Выберите диалог', chooseDialogHint:'Здесь появится переписка с клиентом.', incoming:'Клиент', phoneAuthor:'С телефона',
  waiting_us:'Ждёт ответа', waiting_customer:'Ждём клиента', attachment:'Скачать вложение', deleted:'Удалено', edited:'Изменено',
  receiveOnly:'На этом этапе доступен приём сообщений. Отправка будет подключена отдельно.',
  boardsHint:'Рабочие процессы вашей команды', boardsStage:'Доски подготовлены. Создание и ведение задач — следующий этап.', terminal:'Завершающий статус',
  settingsHint:'Подключения и защищённые настройки системы', wazzup:'WhatsApp / Wazzup', wazzupHint:'Подключите канал и включите доставку сообщений в CRM.',
  secretsHint:'Секреты хранятся зашифрованными. Сохранённые значения показываются только маской.', save:'Сохранить', saved:'Сохранено',
  configured:'Задан', notConfigured:'Не задан', replaceSecret:'Введите новое значение для замены', clearSecret:'Удалить значение',
  testConnection:'Проверить подключение', subscribe:'Подписать вебхуки', subscribed:'Подписка активна', notSubscribed:'Подписка не активна',
  noChannels:'Нет доступных каналов', lastTest:'Тестовый вебхук', lastWebhook:'Последний вебхук', pending:'В очереди', never:'Ещё не получен',
  otherSubscription:'Wazzup подключён к другой CRM. Замена подписки прекратит доставку вебхуков в неё. Продолжить?',
  eventLog:'Последние события', noEvents:'Событий пока нет', received:'Получено', processed:'Обработано', test:'Тест', error:'Повтор', failed:'Ошибка',
  futureIntegrations:'Ключи для следующих подключений', futureHint:'Значения можно сохранить заранее. Обработка через 1С, OpenAI и SMTP на этом этапе не включена.',
  'tasks.timezone':'Часовой пояс напоминаний (например, Asia/Seoul)', 'tasks.reminder_hour':'Час напоминания о просрочке (0–23)', 'tasks.workdays':'Рабочие дни (1 — пн, 7 — вс; через запятую)',
  'inbox.first_messages_per_hour':'Лимит первых сообщений новым номерам за час', 'inbox.auto_close_days':'Закрывать диалоги без сообщений через дней',
  'wazzup.api_key':'Ключ API Wazzup', 'wazzup.default_channel_id':'Канал по умолчанию', 'openai.api_key':'Ключ OpenAI',
  'onec.base_url':'Адрес сервиса 1С', 'onec.username':'Пользователь 1С', 'onec.password':'Пароль 1С', 'smtp.host':'Сервер SMTP', 'smtp.port':'Порт SMTP',
  'smtp.username':'Логин SMTP', 'smtp.password':'Пароль SMTP', 'smtp.from_email':'Email отправителя',
  networkError:'Не удалось связаться с сервером', loadError:'Не удалось загрузить данные', retry:'Повторить',
  more:'Загрузить ещё', securityFoot:'Защищённое соединение · вход по email и паролю'
}}}});
export default i18n;

