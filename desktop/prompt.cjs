window.promptDialog.ready().then(({message,value})=>{document.querySelector('#message').textContent=message;document.querySelector('#value').value=value;document.querySelector('#value').focus();document.querySelector('#value').select();});
document.querySelector('#form').addEventListener('submit',event=>{event.preventDefault();window.promptDialog.answer(document.querySelector('#value').value);});
document.querySelector('#cancel').addEventListener('click',()=>window.promptDialog.answer(null));
document.addEventListener('keydown',event=>{if(event.key==='Escape')window.promptDialog.answer(null);});
