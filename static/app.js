'use strict';
const $=id=>document.getElementById(id);
const labels={positive:'Позитивная',negative:'Негативная',neutral:'Нейтральная'};
const categories={insufficient:'Недостаточно данных',positive:'Преобладает позитив',negative:'Преобладает негатив',neutral:'Преобладает нейтральность',mixed:'Смешанная тональность'};
const colors={positive:'#9b75bd',neutral:'#b7adc8',negative:'#d89bb6'};
const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
const pct=n=>`${Math.round(n*100)}%`;
const date=value=>new Date(value).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
let token=localStorage.getItem('dialog-token')||sessionStorage.getItem('dialog-token');
let me=null,socket=null,registration=false,currentView='dialogs',currentConversation=null;
let dialogs=[],people=[],selectedPeer=null,jobs=[],stats=null,connected=false,reconnectTimer=null,toastTimer=null,pending=null;
let navigation=0,historyLoading=false,incoming=[],conversationRefresh=0;
const messageIds=new Set(),drafts=new Map();
let autoPreviewJob=null,previewJob=null,batchPreviewGeneration=0;
let accessTimer=null,generalClearedThrough=0,clearingGeneral=false,deleteTarget=null,deletingUser=false;

function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6000);}
async function api(url,options={}){
  const headers={...options.headers};if(token)headers.Authorization=`Bearer ${token}`;
  if(options.body&&!(options.body instanceof FormData))headers['Content-Type']='application/json';
  const r=await fetch(url,{...options,headers});
  if(!r.ok){let data={};try{data=await r.json();}catch{}if(r.status===401&&me)await exit(false);throw new Error(typeof data.detail==='string'?data.detail:'Не удалось выполнить запрос. Проверьте данные и соединение.');}
  return r;
}
async function json(url,options){return(await api(url,options)).json();}
function loading(button,on,label){button.disabled=on;if(on){button.dataset.old=button.textContent;button.textContent=label;}else if(button.dataset.old)button.textContent=button.dataset.old;}
function setSendState(){const readOnly=!!currentConversation?.read_only;$('send-button').disabled=!connected||historyLoading||!currentConversation||!!pending||readOnly;$('message-text').disabled=readOnly;$('message-text').placeholder=readOnly?'Участник удалён. Диалог доступен только для просмотра.':'Напишите сообщение…';}
async function modelStatus(){try{const h=await json('/api/health');$('login-model').textContent=h.model_ready?'Модель готова · анализ выполняется на вашем сервере':h.model_error;$('model-status').textContent=h.model_ready?'Модель готова':'Модель недоступна';$('model-dot').classList.toggle('bad',!h.model_ready);}catch{$('login-model').textContent='Сервер недоступен. Запустите start.bat.';}}

$('auth-toggle').onclick=()=>{registration=!registration;$('auth-title').textContent=registration?'Создать аккаунт':'С возвращением';$('auth-subtitle').textContent=registration?'Выберите имя и пароль для сохранения переписки.':'Войдите, чтобы выбрать диалог или начать новый.';$('auth-submit').textContent=registration?'Создать аккаунт':'Войти';$('auth-toggle').textContent=registration?'У меня уже есть аккаунт':'Создать аккаунт';$('password').autocomplete=registration?'new-password':'current-password';$('auth-error').textContent='';};
$('login-form').onsubmit=async e=>{e.preventDefault();$('auth-error').textContent='';loading($('auth-submit'),true,'Подождите…');try{const r=await json(`/api/auth/${registration?'register':'login'}`,{method:'POST',body:JSON.stringify({name:$('name').value.trim(),password:$('password').value})});token=r.token;localStorage.removeItem('dialog-token');sessionStorage.removeItem('dialog-token');($('remember').checked?localStorage:sessionStorage).setItem('dialog-token',token);$('password').value='';await enter(r.user);}catch(ex){$('auth-error').textContent=ex.message;}finally{loading($('auth-submit'),false);}};
async function enter(user){resetBatchPreview();me=user;$('my-role').hidden=!user.is_admin;generalClearedThrough=0;currentConversation=null;dialogs=[];drafts.clear();messageIds.clear();$('messages').replaceChildren();$('login-screen').hidden=true;$('workspace').hidden=false;$('my-name').textContent=user.name;$('my-avatar').textContent=user.name[0].toUpperCase();showView('dialogs');connect();modelStatus();refreshMyProfile();}
async function exit(revoke=true){resetBatchPreview();if(revoke)try{await api('/api/logout',{method:'POST'});}catch{}me=null;token=null;localStorage.removeItem('dialog-token');sessionStorage.removeItem('dialog-token');clearTimeout(reconnectTimer);if(socket){socket.onclose=null;socket.close();socket=null;}connected=false;pending=null;currentConversation=null;navigation++;drafts.clear();dialogs=[];people=[];jobs=[];$('new-dialog-modal').close();$('access-modal').close();$('clear-general-modal').close();$('export-chat-modal').close();$('delete-user-modal').close();$('delete-user-password').value='';deleteTarget=null;$('clear-password').value='';clearInterval(accessTimer);$('workspace').hidden=true;$('login-screen').hidden=false;$('messages').replaceChildren();}
$('logout').onclick=()=>exit();
const accessDialog=$('access-modal');
$('show-access').onclick=()=>{accessDialog.showModal();refreshAccess();clearInterval(accessTimer);accessTimer=setInterval(()=>{if(me&&accessDialog.open)refreshAccess();else clearInterval(accessTimer);},2500);};
$('close-access').onclick=()=>accessDialog.close();
accessDialog.addEventListener('close',()=>clearInterval(accessTimer));
async function refreshAccess(){try{const info=await json('/api/access'),internet=info.internet;const statuses={disabled:'Для интернет-ссылки запустите start_internet.bat.',starting:'Подготавливаем интернет-доступ…',connecting:'Соединяемся с сервисом. Обычно ссылка появляется в течение минуты.',online:'Интернет-ссылка готова. Её можно открыть через мобильный интернет.',stopped:'Туннель остановлен. Запустите start_internet.bat для новой ссылки.'};$('internet-status').textContent=internet.error||statuses[internet.status]||'Подключение недоступно.';$('internet-link-block').hidden=internet.status!=='online';$('internet-url').value=internet.url||'';const box=$('lan-links');box.replaceChildren();if(info.lan_urls.length){info.lan_urls.forEach(url=>{const a=node('a','',url);a.href=url;a.target='_blank';a.rel='noopener noreferrer';box.append(a);});}else box.append(node('p','muted',info.lan_enabled?'Адрес можно посмотреть командой ipconfig: IPv4 у Wi-Fi или Ethernet.':'Для доступа по Wi-Fi запустите start_network.bat или start_internet.bat.'));}catch(e){$('internet-status').textContent=e.message;}}
$('copy-internet').onclick=async()=>{try{if(!navigator.clipboard)throw new Error();await navigator.clipboard.writeText($('internet-url').value);toast('Ссылка скопирована. Отправьте её на телефон.');}catch{$('internet-url').focus();$('internet-url').select();toast('Ссылка выделена. Нажмите Ctrl+C, чтобы скопировать.');}};

function connect(){if(!me)return;clearTimeout(reconnectTimer);const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`);socket=ws;connected=false;setSendState();$('connection-status').textContent='Подключение…';
  ws.onopen=()=>ws.send(JSON.stringify({type:'auth',token}));
  ws.onmessage=e=>{if(ws!==socket||!me)return;const d=JSON.parse(e.data);
    if(d.type==='welcome'){connected=true;$('connection-status').textContent='В сети';setOnline(d.online);dialogs=d.conversations;renderDialogs();updateStats(d.stats);setSendState();if(currentConversation&&currentView==='chat')openConversation(currentConversation.id);}
    if(d.type==='message'){const m=d.message;if(pending&&m.user_id===me.id&&m.conversation_id===pending.cid){pending=null;setSendState();}
      if(currentView==='chat'&&currentConversation?.id===m.conversation_id){if(historyLoading)incoming.push(m);else{appendMessage(m);scrollChat();markRead(m.id);}}
      refreshDialogs();refreshMyProfile();if(currentView==='users')refreshUsers();}
    if(d.type==='user_deleted')handleUserDeleted(d.user_id);
    if(d.type==='history_cleared')applyGeneralClear(d);
    if(d.type==='conversations_changed')refreshDialogs();
    if(d.type==='presence')setOnline(d.online);
    if(d.type==='stats'){updateStats(d.stats);refreshMyProfile();if(currentView==='users')refreshUsers();}
    if(d.type==='batch_progress'){const i=jobs.findIndex(j=>j.id===d.job.id);if(i>=0)jobs[i]=d.job;else jobs.unshift(d.job);renderJobs();if(d.job.status==='done'){toast(`Файл «${d.job.filename}» обработан.`);refreshMyProfile();maybeAutoPreviewBatch();}}
    if(d.type==='error'){restorePending();toast(d.message);}
  };
  ws.onclose=async e=>{if(ws!==socket)return;connected=false;restorePending();setSendState();$('connection-status').textContent='Нет соединения';if(e.code===1008){toast(e.reason==='Аккаунт удалён администратором.'?e.reason:'Сеанс завершён. Войдите ещё раз.');await exit(false);}else if(me)reconnectTimer=setTimeout(connect,2500);};
  ws.onerror=()=>{$('connection-status').textContent='Переподключение…';};
}
function setOnline(n){$('online-count').textContent=n;}
function restorePending(){if(pending){drafts.set(pending.cid,pending.text);if(currentConversation?.id===pending.cid&&!$('message-text').value){$('message-text').value=pending.text;updateChars();}pending=null;}setSendState();}

const views={dialogs:['ПЕРЕПИСКА','Мои диалоги','Выберите сохранённую переписку или начните новую.'],chat:['ПЕРЕПИСКА','Диалог','Сообщения сохраняются, и к ним можно вернуться.'],analysis:['АНАЛИЗ','Анализ текста','Проверьте тональность отдельного комментария.'],batch:['АНАЛИЗ','Обработка файлов','Загружайте комментарии и скачивайте готовую разметку.'],users:['УЧАСТНИКИ','Пользователи','Начните разговор и посмотрите профили общения.'],stats:['АНАЛИТИКА','Статистика','Активность и тональность сообщений во всех чатах.']};
function showView(view){if(view==='general'){openConversation('general');return;}if(currentView==='chat'&&currentConversation)drafts.set(currentConversation.id,$('message-text').value);
  currentView=view;navigation++;document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==`view-${view}`);document.querySelectorAll('.nav-button').forEach(el=>el.classList.toggle('active',el.dataset.view===(view==='chat'?(currentConversation?.kind==='group'?'general':'dialogs'):view)));
  const v=views[view];$('page-eyebrow').textContent=v[0];$('page-title').textContent=v[1];$('page-subtitle').textContent=v[2];$('new-dialog-top').hidden=view!=='dialogs';
  if(view==='dialogs')refreshDialogs();if(view==='users')refreshUsers();if(view==='batch')refreshJobs();if(view==='stats')json('/api/stats').then(updateStats).catch(e=>toast(e.message));
}
document.querySelectorAll('.nav-button').forEach(b=>b.onclick=()=>showView(b.dataset.view));
$('brand-home').onclick=$('back-dialogs').onclick=()=>showView('dialogs');$('open-general').onclick=()=>openConversation('general');
const peerOf=c=>c.participants.find(p=>p.id!==me.id);
async function refreshDialogs(){const generation=++conversationRefresh;try{const list=await json('/api/conversations');if(!me||generation!==conversationRefresh)return;dialogs=list;renderDialogs();if($('new-dialog-modal').open&&selectedPeer)renderExisting();}catch(e){if(me)toast(e.message);}}
function renderDialogs(){const q=$('dialog-search').value.toLowerCase().trim(),box=$('dialog-list');box.replaceChildren();$('dialog-count').textContent=dialogs.length;
  const unread=dialogs.reduce((a,d)=>a+d.unread,0);$('unread-total').hidden=!unread;$('unread-total').textContent=unread;
  const filtered=dialogs.filter(c=>`${peerOf(c)?.name} ${c.title}`.toLowerCase().includes(q));
  if(!filtered.length){const empty=node('div','empty-state');empty.append(node('span','empty-symbol','↔'),node('h3','',q?'Ничего не найдено':'Начните первый разговор'),node('p','',q?'Попробуйте другое имя или название.':'Личные диалоги появятся здесь. Старую общую переписку можно открыть справа.'));if(!q){const b=node('button','primary','+ Новый диалог');b.onclick=()=>openNewDialog();empty.append(b);}box.append(empty);return;}
  filtered.forEach(c=>{const peer=peerOf(c),row=node('button','dialog-row');row.type='button';row.setAttribute('aria-label',`Открыть диалог: ${c.title}, ${peer?.name||'участник'}`);row.append(node('span','avatar',peer?.name[0].toUpperCase()||'?'));const body=node('div','dialog-main');body.append(node('h3','',peer?.name||'Пользователь'),node('p','',c.last_message?.text||'Пока нет сообщений'),node('div','dialog-subline',`${c.title} · ${c.message_count} сообщ.`));const meta=node('div','dialog-row-meta');meta.append(node('time','',date(c.last_message?.created_at||c.created_at)));if(c.unread)meta.append(node('span','unread-pill',c.unread));else meta.append(node('span','','Открыть →'));row.append(body,meta);row.onclick=()=>openConversation(c.id);box.append(row);});
}
$('dialog-search').oninput=renderDialogs;

async function openConversation(cid){if(currentConversation&&currentView==='chat')drafts.set(currentConversation.id,$('message-text').value);currentConversation=dialogs.find(c=>c.id===cid)||{id:cid,kind:cid==='general'?'group':'direct',participants:[],title:''};showView('chat');const generation=navigation;historyLoading=true;incoming=[];messageIds.clear();$('messages').replaceChildren(node('div','empty-state','Загружаем переписку…'));$('chat-person').textContent='Загрузка…';$('chat-detail').textContent='';$('new-with-peer').hidden=true;$('clear-general').hidden=true;$('export-chat').disabled=true;$('message-text').value=drafts.get(cid)||'';updateChars();setSendState();
  try{const [c,messages]=await Promise.all([json(`/api/conversations/${cid}`),json(`/api/history?conversation_id=${encodeURIComponent(cid)}`)]);if(generation!==navigation||currentConversation?.id!==cid)return;currentConversation=c;const peer=c.kind==='direct'?peerOf(c):null;$('page-title').textContent=c.kind==='group'?'Общий чат':c.title;$('chat-person').textContent=peer?.name||'Общий чат';$('chat-avatar').textContent=peer?peer.name[0].toUpperCase():'#';$('chat-detail').textContent=c.kind==='group'?'Все участники · сохранённая общая переписка':`${c.title} · создан ${date(c.created_at)}`;if(c.read_only)$('chat-detail').textContent='Участник удалён · диалог доступен только для просмотра';$('new-with-peer').hidden=!peer||!!peer.deleted;$('clear-general').hidden=cid!=='general'||!me.is_admin;$('export-chat').disabled=false;$('new-with-peer').onclick=()=>openNewDialog(peer.id);
    $('messages').replaceChildren();[...messages,...incoming].sort((a,b)=>a.id-b.id).forEach(appendMessage);if(!messageIds.size)$('messages').append(node('div','empty-state','Начните разговор. Первое сообщение сохранится в этом диалоге.'));historyLoading=false;incoming=[];addHistoryButton(messages,cid);setSendState();scrollChat();const ids=[...messageIds];if(ids.length)markRead(Math.max(...ids));
  }catch(e){if(generation!==navigation)return;historyLoading=false;currentConversation=null;setSendState();$('messages').replaceChildren(node('div','empty-state',e.message));toast(e.message);}}
function appendMessage(m){if(messageIds.has(m.id)||(m.conversation_id==='general'&&m.id<=generalClearedThrough))return;messageIds.add(m.id);$('messages').querySelector('.empty-state')?.remove();const item=node('article',`message${m.user_id===me.id?' own':''}`);item.dataset.mid=m.id;item.append(node('span','avatar',m.name[0].toUpperCase()));const body=node('div','message-body'),meta=node('div','message-meta'),analysis=node('div','message-analysis');meta.append(node('b','',m.name),node('time','',date(m.created_at)));analysis.append(node('span',`badge ${m.label}`,labels[m.label]),node('span','score-caption',`${pct(m.confidence)}${m.truncated?' · текст усечён':''}`));body.append(meta,node('div','bubble',m.text),analysis);item.append(body);$('messages').append(item);}
function scrollChat(){$('messages').scrollTop=$('messages').scrollHeight;}
function addHistoryButton(messages,cid){const generation=navigation;$('messages').querySelector('.history-button')?.remove();if(messages.length<100)return;const b=node('button','history-button','Показать более ранние сообщения');b.type='button';b.onclick=async()=>{b.disabled=true;try{const old=await json(`/api/history?conversation_id=${encodeURIComponent(cid)}&before=${messages[0].id}`);if(generation!==navigation||currentView!=='chat'||currentConversation?.id!==cid)return;const container=$('messages'),height=container.scrollHeight,existing=[...container.children].filter(n=>n!==b);container.replaceChildren();old.forEach(appendMessage);existing.forEach(n=>container.append(n));addHistoryButton(old,cid);container.scrollTop=container.scrollHeight-height;}catch(e){toast(e.message);b.disabled=false;}};$('messages').prepend(b);}
const exportDialog=$('export-chat-modal');let exportConversation=null;
$('export-chat').onclick=()=>{if(!currentConversation||historyLoading)return;exportConversation=currentConversation.id;$('export-chat-error').textContent='';exportDialog.showModal();};
$('close-export-chat').onclick=$('cancel-export-chat').onclick=()=>exportDialog.close();
$('export-chat-form').onsubmit=async e=>{
  e.preventDefault();if(!exportConversation)return;const cid=exportConversation,format=$('export-format').value;
  loading($('download-chat'),true,'Готовим…');$('export-chat-error').textContent='';
  try{const response=await api(`/api/conversations/${encodeURIComponent(cid)}/download?format=${format}`),url=URL.createObjectURL(await response.blob()),a=node('a');a.href=url;a.download=`chat-${cid==='general'?'general':'dialog'}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);exportDialog.close();toast('Переписка подготовлена для скачивания.');}
  catch(ex){$('export-chat-error').textContent=ex.message;}
  finally{loading($('download-chat'),false);}
};
function applyGeneralClear(result){
  if(result.cleared_through_id<=generalClearedThrough)return;
  generalClearedThrough=result.cleared_through_id;
  // повторное чтение отменяет устаревшие запросы истории и сохраняет новые сообщения
  if(currentView==='chat'&&currentConversation?.id==='general')openConversation('general');
  refreshMyProfile();if(currentView==='users')refreshUsers();
  toast(`Общий чат очищен. Удалено сообщений: ${result.deleted}.`);
}
const clearDialog=$('clear-general-modal');
$('clear-general').onclick=()=>{
  if(currentConversation?.id!=='general'||!me.is_admin)return;
  $('clear-general-form').reset();$('clear-error').textContent='';clearDialog.showModal();
};
function closeClearDialog(){if(!clearingGeneral)clearDialog.close();}
$('close-clear-general').onclick=$('cancel-clear-general').onclick=closeClearDialog;
clearDialog.oncancel=e=>{if(clearingGeneral)e.preventDefault();};
clearDialog.onclose=()=>{$('clear-password').value='';};
$('clear-general-form').onsubmit=async e=>{
  e.preventDefault();if(clearingGeneral)return;
  clearingGeneral=true;$('clear-error').textContent='';loading($('confirm-clear-general'),true,'Удаляем…');
  $('cancel-clear-general').disabled=$('close-clear-general').disabled=true;
  try{
    const result=await json('/api/general/clear',{method:'POST',body:JSON.stringify({password:$('clear-password').value,confirmed:$('clear-confirmed').checked})});
    clearDialog.close();applyGeneralClear(result);
    if(!result.deleted)toast('Общий чат уже пуст.');
    json('/api/stats').then(updateStats).catch(()=>{});
  }catch(ex){if(me){$('clear-error').textContent=ex.message;$('clear-password').focus();}}
  finally{clearingGeneral=false;$('clear-password').value='';loading($('confirm-clear-general'),false);$('cancel-clear-general').disabled=$('close-clear-general').disabled=false;}
};
async function markRead(lastId){if(!currentConversation||currentConversation.kind==='group')return;const cid=currentConversation.id;try{await json(`/api/conversations/${cid}/read`,{method:'POST',body:JSON.stringify({last_id:lastId})});}catch{}}
function updateChars(){$('char-count').textContent=`${$('message-text').value.length} / 2000`;}
$('message-text').oninput=()=>{updateChars();if(currentConversation)drafts.set(currentConversation.id,$('message-text').value);};
$('message-text').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('message-form').requestSubmit();}};
$('message-form').onsubmit=e=>{e.preventDefault();const text=$('message-text').value.trim();if(!text||pending||historyLoading||!currentConversation||currentConversation.read_only)return;if(!connected){toast('Подождите подключения к серверу.');return;}pending={cid:currentConversation.id,text};socket.send(JSON.stringify({type:'message',text,conversation_id:currentConversation.id}));drafts.delete(currentConversation.id);$('message-text').value='';updateChars();setSendState();};

async function openNewDialog(peerId=null){selectedPeer=null;$('dialog-title').value='';$('person-search').value='';$('dialog-error').textContent='';$('selected-person-block').hidden=true;$('create-dialog').disabled=true;$('person-list').replaceChildren(node('p','muted','Загружаем пользователей…'));$('new-dialog-modal').showModal();try{people=(await json('/api/users')).filter(p=>p.id!==me.id);renderPeople();if(peerId)selectPeer(peerId);}catch(e){$('dialog-error').textContent=e.message;}}
$('new-dialog-top').onclick=()=>openNewDialog();
function closeNewDialog(){$('new-dialog-modal').close();}
$('close-dialog-modal').onclick=$('cancel-dialog').onclick=closeNewDialog;
function renderPeople(){const box=$('person-list'),q=$('person-search').value.toLowerCase();box.replaceChildren();const filtered=people.filter(p=>p.name.toLowerCase().includes(q));if(!filtered.length)box.append(node('p','muted',people.length?'Пользователь не найден.':'Других участников пока нет. Предложите собеседнику зарегистрироваться.'));filtered.forEach(p=>{const b=node('button',`person-option${p.id===selectedPeer?' selected':''}`);b.type='button';b.append(node('span','avatar',p.name[0].toUpperCase()),node('span','',p.name));b.onclick=()=>selectPeer(p.id);box.append(b);});}
function selectPeer(id){selectedPeer=id;$('selected-person-block').hidden=false;$('selected-person').textContent=`Собеседник: ${people.find(p=>p.id===id)?.name}`;$('create-dialog').disabled=false;renderPeople();renderExisting();}
$('person-search').oninput=renderPeople;
function renderExisting(){const box=$('existing-dialogs'),existing=dialogs.filter(c=>peerOf(c)?.id===selectedPeer);box.replaceChildren();if(!existing.length)return;box.append(node('p','existing-label','У вас уже есть переписки с этим пользователем:'));const list=node('div','existing-list');existing.forEach(c=>{const row=node('div','existing-item'),desc=node('div');desc.append(node('b','',c.title),node('small','',`${c.message_count} сообщ. · ${date(c.created_at)}`));const b=node('button','text-button','Продолжить');b.type='button';b.onclick=()=>{closeNewDialog();openConversation(c.id);};row.append(desc,b);list.append(row);});box.append(list);}
$('new-dialog-form').onsubmit=async e=>{e.preventDefault();if(!selectedPeer)return;loading($('create-dialog'),true,'Создаём…');try{const c=await json('/api/conversations',{method:'POST',body:JSON.stringify({peer_id:selectedPeer,title:$('dialog-title').value.trim()})});if(!dialogs.some(d=>d.id===c.id))dialogs.unshift(c);closeNewDialog();await openConversation(c.id);renderDialogs();}catch(e){$('dialog-error').textContent=e.message;}finally{loading($('create-dialog'),false);}};

function updateStats(s){stats=s;if(currentView==='stats')renderStats();}
async function refreshMyProfile(){if(!me)return;try{const p=await json(`/api/users/${me.id}`);$('my-category').textContent=categories[p.category];$('my-profile-note').textContent=p.total<5?`Для классификации нужно ещё ${5-p.total} комментариев.`:`${p.chat_total} в чатах · ${p.batch_total} из файлов. Порог преобладания — 60%.`;}catch{}}
function scoreBars(container,scores){container.replaceChildren();['positive','neutral','negative'].forEach(k=>{const row=node('div','score-row'),head=node('div','score-header');head.append(node('span','',labels[k]),node('b','',pct(scores[k])));const track=node('div','bar-track'),fill=node('div','bar-fill');fill.style.background=colors[k];fill.style.width=pct(scores[k]);track.append(fill);row.append(head,track);container.append(row);});}
$('analyze-form').onsubmit=async e=>{e.preventDefault();loading($('analyze-button'),true,'Анализируем…');try{const r=await json('/api/analyze',{method:'POST',body:JSON.stringify({text:$('analyze-text').value})});const box=$('analysis-result');box.className='analysis-result has-result';box.replaceChildren(node('span',`badge ${r.label}`,'Тональность комментария'),node('h3','',labels[r.label]),node('p','result-caption',`Оценка выбранного класса: ${pct(r.confidence)}${r.truncated?' · рассмотрены первые 512 токенов':''}`));const bars=node('div');scoreBars(bars,r.scores);box.append(bars);}catch(e){toast(e.message);}finally{loading($('analyze-button'),false);}};
$('batch-file').onchange=()=>{$('file-label').textContent=$('batch-file').files[0]?.name||'Выберите файл';};
['dragenter','dragover'].forEach(ev=>$('drop-zone').addEventListener(ev,()=>$('drop-zone').classList.add('dragover')));['dragleave','drop'].forEach(ev=>$('drop-zone').addEventListener(ev,()=>$('drop-zone').classList.remove('dragover')));
$('upload-form').onsubmit=async e=>{e.preventDefault();const f=$('batch-file').files[0];if(!f)return;if(f.size>1024*1024){toast('Размер файла превышает 1 МБ.');return;}loading($('upload-button'),true,'Загружаем…');try{const form=new FormData();form.append('file',f);const j=await json('/api/batches',{method:'POST',body:form});if(!jobs.some(x=>x.id===j.id))jobs.unshift(j);autoPreviewJob=j.id;renderJobs();maybeAutoPreviewBatch();toast('Файл добавлен в очередь.');$('batch-file').value='';$('file-label').textContent='Выберите файл';}catch(e){toast(e.message);}finally{loading($('upload-button'),false);}};
async function refreshJobs(){try{jobs=await json('/api/batches');renderJobs();maybeAutoPreviewBatch();}catch(e){toast(e.message);}}
$('refresh-jobs').onclick=refreshJobs;
function renderJobs(){const box=$('jobs-list');box.replaceChildren();if(!jobs.length){box.append(node('div','empty-state','Здесь будут ваши файлы и результаты анализа.'));return;}const states={queued:'В очереди',running:'Обрабатывается',done:'Готово',failed:'Ошибка'};jobs.forEach(j=>{const row=node('div','job-row'),desc=node('div','job-description');desc.append(node('b','',j.filename),node('small','',`${date(j.created_at)} · ${states[j.status]} · ${j.processed} / ${j.total}`));const track=node('div','bar-track'),fill=node('div','bar-fill');fill.style.width=pct(j.processed/j.total);fill.style.background=j.status==='failed'?colors.negative:'var(--purple)';track.append(fill);desc.append(track);if(j.error)desc.append(node('p','job-error',j.error));row.append(desc);if(j.status==='done'){const actions=node('div','job-actions');[['Статистика и результаты',()=>preview(j.id)],['Скачать CSV',()=>downloadJob(j.id,'csv')],['Скачать JSON',()=>downloadJob(j.id,'json')]].forEach(([label,fn])=>{const b=node('button','',label);b.onclick=fn;actions.append(b);});row.append(actions);}box.append(row);});}
async function downloadJob(id,format){try{const response=await api(`/api/batches/${id}/download?format=${format}`),url=URL.createObjectURL(await response.blob()),a=node('a');a.href=url;a.download=`result-${id}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){toast(e.message);}}
function table(headers,rows){const t=node('table'),thead=node('thead'),head=node('tr');headers.forEach(h=>head.append(node('th','',h)));thead.append(head);t.append(thead);const body=node('tbody');rows.forEach(cells=>{const row=node('tr');cells.forEach(c=>{const td=node('td');if(c instanceof HTMLElement)td.append(c);else td.textContent=c;row.append(td);});body.append(row);});t.append(body);return t;}
function resetBatchPreview(){
  autoPreviewJob=null;previewJob=null;batchPreviewGeneration++;$('batch-preview').hidden=true;
  $('preview-table').replaceChildren();$('batch-sentiment-chart').replaceChildren();$('batch-metric-cards').replaceChildren();$('batch-result-name').textContent='';$('batch-result-meta').textContent='';$('batch-result-error').textContent='';
}
function maybeAutoPreviewBatch(){
  if(!autoPreviewJob||currentView!=='batch')return;const job=jobs.find(j=>j.id===autoPreviewJob);
  if(job?.status==='failed'){autoPreviewJob=null;return;}
  if(job?.status==='done'){autoPreviewJob=null;preview(job.id);}
}
async function preview(id){
  if(!me)return;autoPreviewJob=null;previewJob=id;const generation=++batchPreviewGeneration,uid=me.id;
  $('batch-preview').hidden=false;$('batch-result-name').textContent=jobs.find(j=>j.id===id)?.filename||'Выбранный файл';$('batch-result-meta').textContent='Загружаем статистику…';$('batch-result-content').hidden=true;$('batch-result-error').textContent='';$('batch-comments-details').open=false;
  try{
    const [summary,rows]=await Promise.all([json(`/api/batches/${id}/stats`),json(`/api/batches/${id}/results`)]);
    if(generation!==batchPreviewGeneration||me?.id!==uid)return;
    $('batch-result-name').textContent=summary.filename;$('batch-result-meta').textContent=`${date(summary.created_at)} · обработка завершена`;
    const cards=$('batch-metric-cards');cards.replaceChildren();
    const share=key=>`${(summary.total?summary.counts[key]/summary.total*100:0).toLocaleString('ru-RU',{maximumFractionDigits:1})}% от файла`;
    [['Всего комментариев',summary.total,'Все обработанные строки','total'],['Позитивные',summary.counts.positive,share('positive'),'positive'],['Нейтральные',summary.counts.neutral,share('neutral'),'neutral'],['Негативные',summary.counts.negative,share('negative'),'negative']].forEach(([title,value,note,key])=>{const card=node('div',`batch-metric-card ${key}`);card.append(node('small','',title),node('strong','',String(value)),node('span','',note));cards.append(card);});
    ChatCharts.renderDonut($('batch-sentiment-chart'),summary.counts,{noun:'комментариев',unit:'комм.',title:'Тональность комментариев выбранного файла',emptyText:'В файле нет обработанных комментариев.'});
    $('batch-comments-label').textContent=`Размеченные комментарии (${rows.length})`;
    $('preview-table').replaceChildren(table(['Комментарий','Тональность','Оценка'],rows.map(r=>[node('div','text-cell',r.text),node('span',`badge ${r.label}`,labels[r.label]),pct(r.confidence)])));
    $('batch-result-content').hidden=false;
    if(currentView==='batch')$('batch-preview').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
  }catch(ex){if(generation===batchPreviewGeneration&&me?.id===uid){$('batch-result-meta').textContent='';$('batch-result-error').textContent=ex.message;}}
}
$('close-preview').onclick=()=>{previewJob=null;batchPreviewGeneration++;$('batch-preview').hidden=true;};
$('batch-download-csv').onclick=()=>{if(previewJob)downloadJob(previewJob,'csv');};
$('batch-download-json').onclick=()=>{if(previewJob)downloadJob(previewJob,'json');};

async function refreshUsers(){try{const users=await json('/api/users');if(!me)return;$('users-table').replaceChildren(table(['Участник','Роль','Всего','Чат / файлы','Позитив','Нейтрально','Негатив','Категория','Действия'],users.map(u=>{
  const name=node('div','user-cell');name.append(node('span','avatar',u.name[0].toUpperCase()),node('b','',u.name));const actions=node('div','user-actions');
  if(u.id===me.id)actions.append(node('span','muted','Вы'));
  else{const write=node('button','text-button','Написать');write.onclick=()=>openNewDialog(u.id);actions.append(write);
    if(me.is_admin&&!u.is_admin){const remove=node('button','text-button remove-user-button','Удалить');remove.setAttribute('aria-label',`Удалить участника ${u.name}`);remove.onclick=()=>openDeleteUser(u);actions.append(remove);}}
  return[name,u.is_admin?'Администратор':'Участник',u.total,`${u.chat_total} / ${u.batch_total}`,pct(u.shares.positive),pct(u.shares.neutral),pct(u.shares.negative),categories[u.category],actions];
})));}catch(e){toast(e.message);}}
function handleUserDeleted(uid){
  people=people.filter(p=>p.id!==uid);if(selectedPeer===uid){selectedPeer=null;$('selected-person-block').hidden=true;$('create-dialog').disabled=true;}
  if($('new-dialog-modal').open)renderPeople();if(deleteTarget?.id===uid)deleteDialog.close();
  refreshDialogs();if(currentView==='users')refreshUsers();
  if(currentView==='chat'&&currentConversation)openConversation(currentConversation.id);
}
const deleteDialog=$('delete-user-modal');
function openDeleteUser(user){if(!me?.is_admin||user.is_admin||user.id===me.id)return;deleteTarget=user;$('delete-user-form').reset();$('delete-user-name').textContent=`Участник: ${user.name}`;$('delete-user-error').textContent='';deleteDialog.showModal();}
function closeDeleteUser(){if(!deletingUser)deleteDialog.close();}
$('close-delete-user').onclick=$('cancel-delete-user').onclick=closeDeleteUser;
deleteDialog.oncancel=e=>{if(deletingUser)e.preventDefault();};
deleteDialog.onclose=()=>{$('delete-user-password').value='';};
$('delete-user-form').onsubmit=async e=>{
  e.preventDefault();if(deletingUser||!deleteTarget)return;const target=deleteTarget;deletingUser=true;$('delete-user-error').textContent='';loading($('confirm-delete-user'),true,'Удаляем…');$('cancel-delete-user').disabled=$('close-delete-user').disabled=true;
  try{await json(`/api/users/${encodeURIComponent(target.id)}`,{method:'DELETE',body:JSON.stringify({password:$('delete-user-password').value,confirmed:$('delete-user-confirmed').checked})});deleteDialog.close();handleUserDeleted(target.id);toast(`Участник «${target.name}» удалён.`);}
  catch(ex){if(me){$('delete-user-error').textContent=ex.message;$('delete-user-password').focus();}}
  finally{deletingUser=false;$('delete-user-password').value='';loading($('confirm-delete-user'),false);$('cancel-delete-user').disabled=$('close-delete-user').disabled=false;}
};
$('refresh-users').onclick=refreshUsers;
function renderActivityStats(){
  if(!stats)return;const period=Number($('stats-period').value),days=ChatCharts.renderActivity($('activity-chart'),stats,period),box=$('timeline');box.replaceChildren();
  const max=Math.max(1,...days.map(d=>d.total));
  days.slice().reverse().forEach(day=>{const row=node('div','timeline-row'),head=node('div','timeline-heading');head.append(node('span','',`${day.day.slice(8,10)}.${day.day.slice(5,7)}`),node('b','',`${day.total} сообщ.`));const bar=node('div','timeline-bar');['positive','neutral','negative'].forEach(key=>{const segment=node('span');segment.style.width=pct(day[key]/max);segment.style.background=colors[key];segment.title=`${labels[key]}: ${day[key]}`;bar.append(segment);});row.append(head,bar);box.append(row);});
}
function renderStats(){
  if(!stats)return;const s=stats;$('metric-cards').replaceChildren();
  [['Сообщений в чатах',s.total],['Участников',s.users],['Позитивных сообщений',`${(s.total?s.counts.positive/s.total*100:0).toLocaleString('ru-RU',{maximumFractionDigits:1})}%`],['Комментариев из файлов',s.batch_total]].forEach(([title,value])=>{const card=node('div','metric-card');card.append(node('small','',title),node('strong','',String(value)));$('metric-cards').append(card);});
  ChatCharts.renderDonut($('sentiment-chart'),s.counts);scoreBars($('stats-bars'),s.shares);renderActivityStats();
}
$('stats-period').onchange=renderActivityStats;
let statsChartWidth=0,statsChartFrame=null;
new ResizeObserver(entries=>{const width=Math.round(entries[0].contentRect.width);if(!width||width===statsChartWidth)return;statsChartWidth=width;if(currentView==='stats'&&stats){cancelAnimationFrame(statsChartFrame);statsChartFrame=requestAnimationFrame(renderActivityStats);}}).observe($('activity-chart'));

setInterval(()=>{if(me&&currentView==='batch'&&jobs.some(j=>['queued','running'].includes(j.status)))refreshJobs();},4000);
document.addEventListener('visibilitychange',()=>{if(me&&!document.hidden){refreshDialogs();if(currentView==='chat'&&!historyLoading&&messageIds.size)markRead(Math.max(...messageIds));}});
async function init(){await modelStatus();if(token){try{await enter(await json('/api/me'));}catch{await exit(false);}}}init();
