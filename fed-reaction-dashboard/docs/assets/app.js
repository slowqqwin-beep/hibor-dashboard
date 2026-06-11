(function(){
  const rows = document.querySelectorAll('tbody tr');
  rows.forEach((row, index) => {
    row.style.setProperty('--delay', `${index * 18}ms`);
  });
})();
