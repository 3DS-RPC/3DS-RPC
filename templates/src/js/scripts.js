//
// Scripts
//

console.log('%cIf you\'re looking at this text, I think you\'re pretty cool!', 'color: #42f578');

function getCookie(name) {
  const cookies = document.cookie.split(';');
  for (let cookie of cookies) {
    cookie = cookie.trim();
    if (cookie.startsWith(name + '=')) {
      return cookie.substring(name.length + 1);
    }
  }
  return '';
}

function eraseCookie(name) {
  document.cookie = name + '=; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT;';
}

function redirectOnFailure(responseText) {
  var reason = responseText.replace(/^failure!\s*/, '').split('\n').join(' ');
  window.location.href = '/error?reason=' + encodeURIComponent(reason);
}

function deleteLogin() {
    eraseCookie('token');
    eraseCookie('user');
    eraseCookie('pfp');
    location.reload();
}

function setupAccountDropdown() {
  const dropdown = document.getElementById('accountDropdown');
  if (!dropdown) return;

  const token = getCookie('token');
  const user = getCookie('user');

  if (token == '') {
    dropdown.querySelector('a[href="/consoles"]').closest('li').remove();
    dropdown.querySelector('a[href="javascript:deleteLogin()"]').closest('li').remove();
  } else {
    dropdown.querySelector('a[href="/discord"]').closest('li').remove();
    const consolesLink = dropdown.querySelector('a[href="/consoles"]');
    consolesLink.textContent = `${user}'s consoles`;
  }
}

var sidebarToggle = document.getElementById('sidebarToggle');
if (sidebarToggle) {
  sidebarToggle.addEventListener('click', function (event) {
    event.preventDefault();
    document.body.classList.toggle('sb-sidenav-toggled');
  });
}

var nav = document.getElementById('navbarDropdown');
const token = getCookie('token');
const user = getCookie('user');
const pfp = getCookie('pfp');
console.log(`User's name: ${user}`);
console.log(`User's pfp: ${pfp}`);
setupAccountDropdown();
if (pfp != '' && nav) {
  const profileImage = document.createElement('img');
  profileImage.className = 'profile-picture';
  profileImage.src = pfp;
  nav.replaceChildren(profileImage);
}
