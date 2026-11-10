export type User = {email:string;role:string;csrf_token:string;must_change_password:boolean;totp_enabled:boolean;totp_verified:boolean};
export type Setting = {key:string;type:string;is_secret:boolean;is_set:boolean;value:string|number};
export type Conversation = {id:string;name:string;phone:string;chat_type:string;channel_id:string;state:string;updated_at:string};
export type Message = {id:string;direction:string;author_type:string;text:string;kind:string;status:string;edited:boolean;deleted:boolean;created_at:string;attachment_url:string|null};
let csrf = '';
export const setCsrf = (value:string) => {csrf=value;};
export class ApiError extends Error {constructor(message:string,public status:number){super(message);}}
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T>{
  const response = await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{})},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json().catch(()=>({detail:'Не удалось прочитать ответ сервера'}));
  if(!response.ok)throw new ApiError(typeof data.detail==='string'?data.detail:'Ошибка запроса',response.status);
  return data as T;
}
