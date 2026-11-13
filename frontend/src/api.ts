export type User = {id:string;email:string;role:string;csrf_token:string;must_change_password:boolean};
export type Setting = {key:string;type:string;is_secret:boolean;is_set:boolean;value:string|number};
export type Conversation = {id:string;customer_id:string|null;name:string;display_name:string;onec_status:string;onec_stale:boolean;phone:string;chat_type:string;channel_id:string;state:string;updated_at:string;assignee_id:string|null;needs_human:boolean;ai_mode:string;order_numbers:string[];unread_count:number;last_message:string;last_status:string;task_updated:boolean;has_ai_draft?:boolean};
export type Message = {id:string;direction:string;author_type:string;text:string;kind:string;status:string;edited:boolean;history?:{body:string;at:string}[];content?:{map_url?:string;name?:string;phones?:string[]};deleted:boolean;created_at:string;attachment_url:string|null;attachment_name:string|null;attachment_mime:string|null;author_name:string|null;is_private_note:boolean;error_code:string|null;error_text:string|null;quoted_message_id:string|null;quoted_text:string};
let csrf = '';
export const setCsrf = (value:string) => {csrf=value;};
export class ApiError extends Error {constructor(message:string,public status:number){super(message);}}
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T>{
  const response = await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{})},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json().catch(()=>({detail:'Не удалось прочитать ответ сервера'}));
  if(!response.ok)throw new ApiError(typeof data.detail==='string'?data.detail:'Ошибка запроса',response.status);
  return data as T;
}


export async function uploadFile(file:File,path='/api/uploads'):Promise<{id:string;filename:string;size:number}>{
  const body=new FormData();body.append('file',file);
  const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf},body});
  const data=await response.json();if(!response.ok)throw new ApiError(data.detail||'Ошибка загрузки',response.status);return data;
}
