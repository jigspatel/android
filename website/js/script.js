document.addEventListener("DOMContentLoaded", function () {
  var header = document.querySelector(".site-header");
  var toggle = document.querySelector(".nav-toggle");

  if (toggle && header) {
    toggle.addEventListener("click", function () {
      header.classList.toggle("nav-open");
    });
  }

  var form = document.querySelector(".contact-form");
  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var name = form.querySelector("#name");
      var subject = encodeURIComponent("Website enquiry from " + (name ? name.value : "website visitor"));
      var body = [];
      form.querySelectorAll("input, select, textarea").forEach(function (field) {
        if (field.name) body.push(field.previousElementSibling ? field.previousElementSibling.textContent + ": " + field.value : field.value);
      });
      window.location.href = "mailto:info@joynuts.com.au?subject=" + subject + "&body=" + encodeURIComponent(body.join("\n"));
    });
  }

  var year = document.querySelector(".current-year");
  if (year) year.textContent = new Date().getFullYear();
});
