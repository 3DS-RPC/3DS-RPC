//
// Scripts
//

console.log('%cIf you\'re looking at this text, I think you\'re pretty cool!', 'color: #42f578');

function getCookie(cname) {
  let name = cname + '=';
  let ca = document.cookie.split(';');
  for(let i = 0; i < ca.length; i++) {
    let c = ca[i];
    while (c.charAt(0) == ' ') {
      c = c.substring(1);
    }
    if (c.indexOf(name) == 0) {
      return c.substring(name.length, c.length);
    }
  }
  return '';
}
function eraseCookie(name) {
    document.cookie = name +'=; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT;';
}
function deleteLogin() {
    eraseCookie('token');
    eraseCookie('user');
    eraseCookie('pfp');
    location.reload();
}

var account = document.getElementById('accountDropdown')
var nav = document.getElementById('navbarDropdown')
const token = getCookie('token');
const user = getCookie('user');
const pfp = getCookie('pfp');
console.log(`User's name: ${user}`);
console.log(`User's pfp: ${pfp}`);
if (account) {
  var accountList = account.getElementsByTagName('li');
  if (token == '')
  {
    account.removeChild(accountList[0]);
    account.removeChild(accountList[0]);
  } else {
    account.removeChild(accountList[2]);
    accountList[0].firstChild.text = `${user}'s consoles`;
  }
}
if (pfp != '' && nav) {
  nav.innerHTML = `<img height = '50px' src = '${pfp}' style = 'border-radius: 50%;' />`;
}
