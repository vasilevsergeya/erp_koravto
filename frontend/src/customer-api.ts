export type CustomerLite = {
  id: string;
  version: number;
  name: string;
  display_name: string;
  name_manual: boolean;
  language: string;
  segment: string;
  country: string;
  tags: string[];
  order_numbers: string[];
  default_owner_id: string | null;
  phones: { id: string; value: string; verified: boolean }[];
  conversation_count: number;
  open_conversations: number;
  open_tasks: number;
  first_contact: string | null;
  last_contact: string | null;
};
export type CustomersResult = { items: CustomerLite[]; total: number };
export const openCustomer = (id: string) => {
  sessionStorage.setItem("crm-open-customer", id);
  history.pushState({}, "", `/customers/${id}`);
  window.dispatchEvent(new CustomEvent("crm:customer", { detail: id }));
};
export const openConversation = (id: string) => {
  sessionStorage.setItem("crm-open-conversation", id);
  history.pushState({}, "", "/");
  window.dispatchEvent(new CustomEvent("crm:conversation", { detail: id }));
};
export const newCustomerChat = (phone: string, name: string) => {
  sessionStorage.setItem(
    "crm-new-conversation",
    JSON.stringify({ phone, name }),
  );
  history.pushState({}, "", "/");
  window.dispatchEvent(new CustomEvent("crm:new-conversation"));
};
