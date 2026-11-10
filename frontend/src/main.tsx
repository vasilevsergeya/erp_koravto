import React, {useCallback, useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient, QueryClientProvider, useQuery, useQueryClient} from '@tanstack/react-query';
import {MantineProvider, TextInput, PasswordInput, Button, Alert, Badge, Loader, Select} from '@mantine/core';
import {useTranslation} from 'react-i18next';
import {MessageSquare, LayoutDashboard, Settings, LogOut, ArrowRight, ArrowLeft, ShieldCheck, ChevronRight, Link2, Inbox as InboxIcon, CheckCircle2, KeyRound} from 'lucide-react';
import QRCode from 'qrcode';
import '@mantine/core/styles.css';
import './i18n';
import './style.css';
import {api, ApiError, setCsrf, type User, type Setting, type Conversation, type Message} from './api';

const client = new QueryClient({defaultOptions:{queries:{retry:1,refetchOnWindowFocus:true}}});
const stamp = (value:string|null) => value?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Asia/Seoul'}).format(new Date(value)):'—';
const errText = (error:unknown) => error instanceof Error?error.message:String(error);

function Brand(){return <div className="brand"><span className="brand-icon">K<span/></span><div>KORAVTO<small>CRM WORKSPACE</small></div></div>;}

function Auth({user,onUser}:{user:User|null;onUser:(user:User)=>void}){
  const {t}=useTranslation();const[email,emailSet]=useState('');const[password,passwordSet]=useState('');
  const[code,codeSet]=useState('');const[error,errorSet]=useState('');const[busy,busySet]=useState(false);
  const[enrollment,enrollmentSet]=useState<{secret:string;uri:string}|null>(null);const[qr,qrSet]=useState('');
  const passwordStep=!!user?.must_change_password;const setupStep=!!user&&!passwordStep&&!user.totp_enabled;
  useEffect(()=>{if(setupStep&&!enrollment){api<{secret:string;uri:string}>('/api/auth/totp/setup','POST').then(enrollmentSet).catch(e=>errorSet(errText(e)));}},[setupStep,enrollment]);
  useEffect(()=>{if(enrollment)void QRCode.toDataURL(enrollment.uri,{width:210,margin:1}).then(qrSet);},[enrollment]);
  async function submit(event:React.FormEvent){event.preventDefault();errorSet('');busySet(true);try{
    const path=!user?'/api/auth/login':passwordStep?'/api/auth/password':'/api/auth/totp/verify';
    const body=!user?{email,password}:passwordStep?{password}:{code};
    const next=await api<User>(path,'POST',body);setCsrf(next.csrf_token);passwordSet('');codeSet('');onUser(next);
  }catch(e){errorSet(errText(e));}finally{busySet(false);}}
  const title=!user?t('signIn'):passwordStep?t('passwordTitle'):setupStep?t('totpTitle'):t('totpCodeTitle');
  const hint=!user?t('signInHint'):passwordStep?t('passwordHint'):setupStep?t('totpHint'):t('totpCodeHint');
  return <div className="auth-layout"><aside className="auth-story"><Brand/><div className="story-center"><div className="eyebrow">KOREAN PARTS · TEAM</div><h1>Ближе к клиентам.<br/><span>Вместе в работе.</span></h1><p>Единое пространство для общения<br/>и ежедневных задач команды.</p><div className="story-graphic"><div className="graphic-line"/><div className="graphic-card"><MessageSquare size={22}/><span>Разговор начинается<br/><b>с внимания</b></span><span className="graphic-dot"/></div><div className="graphic-small"><CheckCircle2 size={18}/> На связи с командой</div></div></div><div className="story-foot">KORAVTO / 2026</div></aside><main className="auth-main"><div className="mobile-brand"><Brand/></div><div className="auth-card"><div className="auth-symbol">{user?<ShieldCheck/>:<KeyRound/>}</div><div className="eyebrow dark">{t(user?'security':'authTag')}</div><h2>{title}</h2><p className="muted">{hint}</p>{user&&<div className="steps"><span className={!passwordStep?'done':''}>01 {t('step1')}</span><span className={!passwordStep?'active':''}>02 {t('step2')}</span></div>}
    {error&&<Alert color="red" mb="md" role="alert">{error}</Alert>}<form onSubmit={submit}>
    {!user&&<TextInput label={t('email')} type="email" autoComplete="username" value={email} onChange={e=>emailSet(e.currentTarget.value)} required size="md" mb="md"/>}
    {(!user||passwordStep)?<PasswordInput label={t(passwordStep?'newPassword':'password')} autoComplete={passwordStep?'new-password':'current-password'} value={password} minLength={passwordStep?12:1} onChange={e=>passwordSet(e.currentTarget.value)} required size="md" mb="lg"/>:<>
      {setupStep&&enrollment&&<div className="totp-setup">{qr&&<img src={qr} alt="QR-код настройки TOTP"/>}<details><summary>{t('manualKey')}</summary><code>{enrollment.secret}</code></details></div>}
      <TextInput label={t('totpCode')} autoComplete="one-time-code" inputMode="numeric" maxLength={6} value={code} onChange={e=>codeSet(e.currentTarget.value.replace(/\D/g,''))} required size="md" mb="lg"/>
    </>}<Button type="submit" fullWidth size="md" loading={busy} rightSection={<ArrowRight size={17}/>}>{t(!user?'login':passwordStep?'savePassword':'confirm')}</Button></form><div className="security-foot"><ShieldCheck size={14}/>{t('securityFoot')}</div></div></main></div>;
}

function Inbox({openSettings}:{openSettings:()=>void}){
  const {t}=useTranslation();const[selected,select]=useState<string|null>(null);const[page,pageSet]=useState(0);
  const conversations=useQuery({queryKey:['conversations',page],queryFn:()=>api<Conversation[]>('/api/conversations?offset='+page*50),refetchInterval:5000});
  const messages=useQuery({queryKey:['messages',selected],queryFn:()=>api<Message[]>('/api/conversations/'+selected+'/messages'),enabled:!!selected,refetchInterval:5000});
  const current=conversations.data?.find(c=>c.id===selected);
  if(conversations.isLoading)return <div className="loading"><Loader/></div>;
  if(conversations.error)return <Alert color="red">{errText(conversations.error)}</Alert>;
  return <div className="inbox-panel"><aside className="conversation-list"><div className="list-heading">{t('inbox')}<Badge color="gray" variant="light">{conversations.data?.length??0}</Badge></div>{!conversations.data?.length?<div className="empty-list"><InboxIcon size={28}/><h3>{t('noDialogs')}</h3><p>{t('noDialogsHint')}</p><Button variant="light" onClick={openSettings}>{t('openSettings')}</Button></div>:conversations.data?.map(c=><button key={c.id} className={'conversation '+(selected===c.id?'selected':'')} onClick={()=>select(c.id)}><span className="avatar">{(c.name||c.phone||'?').slice(0,1)}</span><span><b>{c.name||c.phone}</b><small>{c.phone}</small><em>{t(c.state)}</em></span><ChevronRight size={16}/></button>)}{(page>0||conversations.data?.length===50)&&<div className="page-buttons"><Button variant="subtle" disabled={!page} onClick={()=>pageSet(page-1)}>←</Button><Button variant="subtle" disabled={conversations.data?.length!==50} onClick={()=>pageSet(page+1)}>→</Button></div>}</aside><section className="message-area">{!selected?<div className="empty-chat"><div className="empty-icon"><MessageSquare size={34}/></div><h3>{t('chooseDialog')}</h3><p>{t('chooseDialogHint')}</p></div>:<><header className="chat-header"><button className="chat-back" onClick={()=>select(null)} aria-label="Назад к диалогам"><ArrowLeft size={19}/></button><b>{current?.name||current?.phone}</b><Badge variant="light" color="teal">{current?.chat_type}</Badge></header><div className="messages">{messages.error&&<Alert color="red">{errText(messages.error)}</Alert>}{messages.data?.map(m=><article key={m.id} className={'message '+m.direction}><small>{t(m.direction==='in'?'incoming':'phoneAuthor')}</small><div dir="auto">{m.text||'['+m.kind+']'}</div>{m.attachment_url&&<a href={m.attachment_url} target="_blank" rel="noreferrer">{t('attachment')}</a>}<footer>{m.edited&&t('edited')+' · '}{m.deleted&&t('deleted')+' · '}{stamp(m.created_at)} · {m.status}</footer></article>)}</div><div className="receive-note">{t('receiveOnly')}</div></>}</section></div>;
}

type Board = {id:string;name:string;statuses:{name:string;terminal:boolean;color:string}[]};
function Boards(){const{t}=useTranslation();const boards=useQuery({queryKey:['boards'],queryFn:()=>api<Board[]>('/api/boards')});return <><div className="notice"><LayoutDashboard size={18}/>{t('boardsStage')}</div><div className="board-grid">{boards.error&&<Alert color="red">{errText(boards.error)}</Alert>}{boards.data?.map(b=><section className="surface board-card" key={b.id}><LayoutDashboard size={23}/><h3>{b.name}</h3><div className="board-statuses">{b.statuses.map(s=><span key={s.name}><i className={s.terminal?'terminal':''}/>{s.name}{s.terminal&&<CheckCircle2 size={14}/>}</span>)}</div></section>)}</div></>;}

function SettingField({item,onSaved}:{item:Setting;onSaved:()=>void}){
  const{t}=useTranslation();const[value,setValue]=useState(item.is_secret?'':String(item.value));const[busy,setBusy]=useState(false);const[message,setMessage]=useState('');
  useEffect(()=>{setValue(item.is_secret?'':String(item.value));},[item.value,item.is_secret]);
  async function save(next=value){setBusy(true);setMessage('');try{await api('/api/settings/'+item.key,'PUT',{value:item.type==='integer'?Number(next):next});if(item.is_secret)setValue('');onSaved();setMessage(t('saved'));}catch(e){setMessage(errText(e));}finally{setBusy(false);}}
  return <div className="setting-row"><div className="setting-label"><label htmlFor={item.key}>{t(item.key)}</label>{item.is_secret&&<span className={item.is_set?'secret-set':'muted'}>{item.is_set?String(item.value):t('notConfigured')}</span>}</div><div className="setting-control">{item.is_secret?<PasswordInput id={item.key} aria-label={t(item.key)} value={value} onChange={e=>setValue(e.currentTarget.value)} placeholder={t('replaceSecret')} autoComplete="new-password"/>:<TextInput id={item.key} aria-label={t(item.key)} value={value} onChange={e=>setValue(e.currentTarget.value)} type={item.type==='integer'?'number':'text'}/>}<Button variant="light" loading={busy} disabled={item.is_secret&&!value} onClick={()=>void save()}>{t('save')}</Button>{item.is_secret&&item.is_set&&<Button variant="subtle" color="gray" onClick={()=>void save('')} aria-label={t('clearSecret')}>×</Button>}</div>{message&&<small className="field-feedback" role="status">{message}</small>}</div>;
}

type WazzupStatus={last_test_at:string|null;last_webhook_at:string|null;pending:number;events:{id:string;state:string;created_at:string;error:string|null}[]};
type CheckResult={channels:{channelId:string;name:string|null;transport:string;state:string}[];subscribed:boolean;has_other_subscription:boolean};
function SettingsPage(){
  const{t}=useTranslation();const query=useQueryClient();const settings=useQuery({queryKey:['settings'],queryFn:()=>api<Setting[]>('/api/settings')});
  const status=useQuery({queryKey:['wazzup-status'],queryFn:()=>api<WazzupStatus>('/api/settings/wazzup/status'),refetchInterval:5000});
  const[connection,setConnection]=useState<CheckResult|null>(null);const[busy,setBusy]=useState('');const[feedback,setFeedback]=useState('');
  const refresh=()=>{void query.invalidateQueries({queryKey:['settings']});};
  async function check(){setBusy('check');setFeedback('');try{const result=await api<CheckResult>('/api/settings/wazzup/check','POST');setConnection(result);}catch(e){setFeedback(errText(e));}finally{setBusy('');}}
  async function subscribe(replace=false){setBusy('subscribe');setFeedback('');try{const result=await api<{ok:boolean}>('/api/settings/wazzup/subscribe','POST',{replace_existing:replace});setFeedback(t(result.ok?'subscribed':'notSubscribed'));await check();}catch(e){if(e instanceof ApiError&&e.status===409&&!replace){if(window.confirm(t('otherSubscription'))){await subscribe(true);return;}}else setFeedback(errText(e));}finally{setBusy('');}}
  return <div className="settings-page"><section className="surface settings-card"><div className="card-heading"><span className="integration-icon"><MessageSquare/></span><div><h3>{t('wazzup')}</h3><p>{t('wazzupHint')}</p></div>{connection&&<Badge color={connection.subscribed?'teal':'gray'}>{t(connection.subscribed?'subscribed':'notSubscribed')}</Badge>}</div><div className="notice quiet"><ShieldCheck size={17}/>{t('secretsHint')}</div>{settings.error&&<Alert color="red">{errText(settings.error)}</Alert>}{settings.data?.filter(s=>s.key==='wazzup.api_key').map(s=><SettingField key={s.key} item={s} onSaved={refresh}/>)}
  {connection?.channels.length?<div className="setting-row"><Select label={t('wazzup.default_channel_id')} value={String(settings.data?.find(s=>s.key==='wazzup.default_channel_id')?.value||'')} data={connection.channels.map(c=>({value:c.channelId,label:(c.name||c.transport||c.channelId)+' · '+c.state}))} onChange={value=>{void api('/api/settings/wazzup.default_channel_id','PUT',{value:value||''}).then(refresh).catch(e=>setFeedback(errText(e)));}}/></div>:null}
  <div className="integration-actions"><Button variant="default" loading={busy==='check'} disabled={!!busy} leftSection={<Link2 size={16}/>} onClick={()=>void check()}>{t('testConnection')}</Button><Button loading={busy==='subscribe'} disabled={!!busy} onClick={()=>void subscribe()}>{t('subscribe')}</Button></div>{feedback&&<Alert mt="md" role="status">{feedback}</Alert>}{connection&&<div className="channel-list">{connection.channels.length?connection.channels.map(c=><Badge key={c.channelId} variant="light" color={c.state==='active'?'teal':'orange'}>{c.transport} · {c.state}</Badge>):t('noChannels')}</div>}
  <div className="metrics">{[[t('lastTest'),stamp(status.data?.last_test_at||null)],[t('lastWebhook'),stamp(status.data?.last_webhook_at||null)],[t('pending'),status.data?.pending??0]].map(([label,value])=><div key={String(label)}><small>{label}</small><b>{value}</b></div>)}</div></section>
  <section className="surface events-card"><h3>{t('eventLog')}</h3>{!status.data?.events.length?<p className="muted">{t('noEvents')}</p>:<div className="event-list">{status.data.events.map(e=><div key={e.id}><span className={'event-dot '+e.state}/><span>{t(e.state)}</span><small>{stamp(e.created_at)}</small>{e.error&&<small>{e.error}</small>}</div>)}</div>}</section>
  <details className="surface future-settings"><summary>{t('futureIntegrations')}<ChevronRight size={18}/></summary><p className="muted">{t('futureHint')}</p>{settings.data?.filter(s=>!s.key.startsWith('wazzup.')).map(s=><SettingField key={s.key} item={s} onSaved={refresh}/>)}</details></div>;
}

function Workspace({user,onLogout}:{user:User;onLogout:()=>void}){
  const{t}=useTranslation();const[page,setPage]=useState('inbox');const query=useQueryClient();
  const health=useQuery({queryKey:['health'],queryFn:()=>api<{ok:boolean}>('/health'),refetchInterval:15000});
  useEffect(()=>{let socket:WebSocket;let timer:ReturnType<typeof setTimeout>;let disposed=false;function connect(){if(disposed)return;socket=new WebSocket(location.origin.replace(/^http/,'ws')+'/api/ws');socket.onmessage=event=>{try{const payload=JSON.parse(event.data);if(payload.type==='messages.changed'){void query.invalidateQueries({queryKey:['conversations']});void query.invalidateQueries({queryKey:['messages']});}}catch{/* reconnect/poll provides recovery */}};socket.onclose=()=>{if(!disposed)timer=setTimeout(connect,3000);};}connect();return()=>{disposed=true;clearTimeout(timer);socket?.close();};},[query]);
  const pages=[{id:'inbox',icon:MessageSquare},{id:'boards',icon:LayoutDashboard},...(user.role==='admin'?[{id:'settings',icon:Settings}]:[])];
  return <div className="workspace"><aside className="sidebar"><Brand/><div className="nav-label">{t('workspace')}</div><nav>{pages.map(p=><button key={p.id} className={page===p.id?'active':''} onClick={()=>setPage(p.id)}><p.icon size={19}/>{t(p.id)}{page===p.id&&<ChevronRight size={16}/>}</button>)}</nav><div className="sidebar-bottom"><div className="user-avatar">{user.email[0].toUpperCase()}</div><div className="user-meta"><b>{user.email}</b><small>{user.role==='admin'?'Администратор':user.role}</small></div><button className="logout" aria-label={t('logout')} title={t('logout')} onClick={onLogout}><LogOut size={18}/></button></div></aside><main className="main"><header className="topbar"><span className="eyebrow dark">{t('version')}</span><div className={'health '+(health.data?.ok?'healthy':'')}><i/>{t(health.isLoading?'checking':health.data?.ok?'connected':'degraded')}</div></header><div className="page-body"><div className="page-heading"><div><h1>{t(page)}</h1><p>{t(page+'Hint')}</p></div><span className="timezone">SEOUL · {new Intl.DateTimeFormat('ru',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit'}).format(new Date())}</span></div>{page==='inbox'?<Inbox openSettings={()=>setPage('settings')}/>:page==='boards'?<Boards/>:<SettingsPage/>}</div></main></div>;
}

function App(){const[user,userSet]=useState<User|null>(null);const[loading,loadingSet]=useState(true);const[error,errorSet]=useState('');
  const update=useCallback((value:User)=>{setCsrf(value.csrf_token);userSet(value);},[]);
  const load=useCallback(()=>{loadingSet(true);errorSet('');api<User>('/api/auth/me').then(update).catch(e=>{if(!(e instanceof ApiError&&e.status===401))errorSet(errText(e));}).finally(()=>loadingSet(false));},[update]);
  useEffect(()=>{load();},[load]);
  async function logout(){try{await api('/api/auth/logout','POST');}finally{client.clear();userSet(null);setCsrf('');}}
  if(loading)return <div className="loading full"><Loader color="teal"/></div>;
  if(error)return <div className="loading full"><Alert color="red">{error}<Button mt="md" onClick={load}>Повторить</Button></Alert></div>;
  if(!user||user.must_change_password||!user.totp_enabled||!user.totp_verified)return <Auth user={user} onUser={update}/>;
  return <Workspace user={user} onLogout={()=>void logout()}/>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><MantineProvider theme={{primaryColor:'teal',defaultRadius:'md',fontFamily:'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif',headings:{fontFamily:'inherit'}}}><QueryClientProvider client={client}><App/></QueryClientProvider></MantineProvider></React.StrictMode>);
