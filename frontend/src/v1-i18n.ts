import i18n from "./i18n";
const ru = {
  employees: "Сотрудники",
  invite: "Пригласить сотрудника",
  inviteHint: "Одноразовая ссылка для установки пароля действует 24 часа.",
  role: "Роль",
  admin: "Администратор",
  manager: "Менеджер",
  staff: "Сотрудник",
  emailLink: "Отправить ссылку по email (нужен настроенный SMTP)",
  active: "Доступ включён",
  inactive: "Доступ отключён",
  awaitPassword: "Ожидает установки пароля",
  access: "Управление доступом",
  accessHint: "При изменении доступа текущие сеансы сотрудника завершатся.",
  save: "Сохранить",
  resetPassword: "Сбросить пароль",
  resetConfirm:
    "Завершить сеансы сотрудника и заменить старый пароль одноразовой ссылкой?",
  accessLink: "Ссылка для установки пароля",
  expires: "Действует до",
  linkHint:
    "Установите пароль не короче 7 символов. Ссылка используется один раз.",
  mail_sent: "Письмо отправлено.",
  mail_not_requested:
    "Передайте эту ссылку сотруднику. После закрытия окна она больше не отображается.",
  mail_uncertain:
    "Результат отправки письма неизвестен. Повторная отправка не выполнялась.",
  mail_failed:
    "Не удалось отправить письмо. Проверьте SMTP или передайте ссылку сотруднику.",
  setPassword: "Установка пароля",
  password: "Новый пароль",
  passwordSet: "Пароль сохранён. Теперь войдите по email и новому паролю.",
  login: "Перейти ко входу",
  quickReplies: "Быстрые ответы",
  placeholders:
    "Подстановки: {имя} и {номер_заказа}. В диалоге начните сообщение с / или нажмите «Быстрые ответы».",
  addReply: "Добавить ответ",
  edit: "Изменить",
  title: "Название",
  replyText: "Текст ответа",
  delete: "Удалить",
  deleteReply: "Удалить быстрый ответ?",
  order: "Заказ",
  insert: "Вставить в сообщение",
  close: "Закрыть",
  missingPlaceholder:
    "Укажите имя клиента в карточке и выберите привязанный заказ, который нужен для шаблона.",
  aiDraft: "AI-черновик",
  sources: "На чём основан ответ",
  previousVersions: "Предыдущие версии",
  map: "Открыть на карте",
  contact: "Контакт",
  incorrect: "Неверная информация",
  irrelevant: "Не по теме",
  tone: "Не подходит тон",
  other: "Другое",
  comment: "Комментарий",
  rejectReason: "Причина отклонения",
};
const en = {
  employees: "Employees",
  invite: "Invite employee",
  inviteHint: "The single-use password setup link expires in 24 hours.",
  role: "Role",
  admin: "Administrator",
  manager: "Manager",
  staff: "Staff",
  emailLink: "Email the link (requires configured SMTP)",
  active: "Access enabled",
  inactive: "Access disabled",
  awaitPassword: "Password setup pending",
  access: "Manage access",
  accessHint: "Changing access ends the employee's current sessions.",
  save: "Save",
  resetPassword: "Reset password",
  resetConfirm:
    "End employee sessions and replace the password with a single-use setup link?",
  accessLink: "Password setup link",
  expires: "Expires",
  linkHint:
    "Set a password with at least 7 characters. This link can only be used once.",
  mail_sent: "Email sent.",
  mail_not_requested:
    "Share this link with the employee. It will not be shown again after closing this window.",
  mail_uncertain: "Email delivery is uncertain. No automatic retry was made.",
  mail_failed:
    "Email could not be sent. Check SMTP or share the link with the employee.",
  setPassword: "Set password",
  password: "New password",
  passwordSet: "Password saved. Sign in with your email and new password.",
  login: "Go to sign in",
  quickReplies: "Quick replies",
  placeholders:
    "Placeholders: {имя} (name) and {номер_заказа} (order number). Type / in the composer or select Quick replies.",
  addReply: "Add reply",
  edit: "Edit",
  title: "Title",
  replyText: "Reply text",
  delete: "Delete",
  deleteReply: "Delete quick reply?",
  order: "Order",
  insert: "Insert into message",
  close: "Close",
  missingPlaceholder:
    "Set the customer name and select the linked order required by this template.",
  aiDraft: "AI draft",
  sources: "Sources used",
  previousVersions: "Previous versions",
  map: "Open map",
  contact: "Contact",
  incorrect: "Incorrect information",
  irrelevant: "Irrelevant",
  tone: "Unsuitable tone",
  other: "Other",
  comment: "Comment",
  rejectReason: "Rejection reason",
};
i18n.addResourceBundle("ru", "v1", ru, true, true);
i18n.addResourceBundle("en", "v1", en, true, true);
i18n.addResourceBundle(
  "ru",
  "translation",
  {
    employees: ru.employees,
    employeesHint: "Роли, приглашения и доступ к CRM",
  },
  true,
  true,
);
i18n.addResourceBundle(
  "en",
  "translation",
  {
    employees: en.employees,
    employeesHint: "Roles, invitations and CRM access",
  },
  true,
  true,
);
i18n.addResourceBundle("ru", "inbox", { ai_draft: ru.aiDraft }, true, true);
i18n.addResourceBundle("en", "inbox", { ai_draft: en.aiDraft }, true, true);
