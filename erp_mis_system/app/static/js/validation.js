(() => {
  const registerForm = document.querySelector("[data-register-form]");
  if (registerForm) {
    const passwordInput = registerForm.querySelector("[data-password-strength]");
    const confirmInput = registerForm.querySelector("[data-password-confirm]");
    const strengthBar = registerForm.querySelector("[data-strength-bar]");
    const strengthLabel = registerForm.querySelector("[data-strength-label]");
    const confirmMessage = registerForm.querySelector("[data-confirm-message]");

    const updateStrength = () => {
      const value = passwordInput.value;
      const groups = [
        /[a-z]/.test(value),
        /[A-Z]/.test(value),
        /\d/.test(value),
        /[^A-Za-z0-9]/.test(value),
      ].filter(Boolean).length;
      const longEnough = value.length >= 8;
      const score = value.length === 0 ? 0 : (longEnough ? 1 : 0) + groups;
      const percent = Math.min(100, score * 20);
      const strength = !value ? "enter a password" : !longEnough || groups < 3 ? "weak" : score >= 5 ? "strong" : "good";
      const color = strength === "strong" ? "#168267" : strength === "good" ? "#d28a13" : "#dc3545";
      strengthBar.style.width = `${percent}%`;
      strengthBar.style.backgroundColor = color;
      strengthLabel.textContent = `Password strength: ${strength}`;
      passwordInput.setCustomValidity(
        !longEnough || groups < 3
          ? "Use at least 8 characters and at least 3 character types."
          : "",
      );
    };

    const updateConfirmation = () => {
      const matches = confirmInput.value === passwordInput.value;
      confirmInput.setCustomValidity(matches || !confirmInput.value ? "" : "The passwords do not match.");
      confirmMessage.textContent = confirmInput.value ? (matches ? "Passwords match." : "Passwords do not match.") : "";
      confirmMessage.classList.toggle("text-success", matches && Boolean(confirmInput.value));
      confirmMessage.classList.toggle("text-danger", !matches && Boolean(confirmInput.value));
    };

    passwordInput.addEventListener("input", () => {
      updateStrength();
      updateConfirmation();
    });
    confirmInput.addEventListener("input", updateConfirmation);
    registerForm.addEventListener("submit", (event) => {
      updateStrength();
      updateConfirmation();
      if (!registerForm.checkValidity()) {
        event.preventDefault();
        event.stopPropagation();
        registerForm.classList.add("was-validated");
        registerForm.reportValidity();
      }
    });
  }

  document.querySelectorAll("[data-validated-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!form.checkValidity()) {
        event.preventDefault();
        event.stopPropagation();
        form.classList.add("was-validated");
        form.reportValidity();
      }
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    });
  });

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented || !form.checkValidity()) return;
      const submitter = event.submitter || form.querySelector("[type='submit']");
      if (!submitter || submitter.disabled) return;
      submitter.disabled = true;
      submitter.dataset.originalContent = submitter.innerHTML;
      submitter.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Processing…';
      submitter.setAttribute("aria-busy", "true");
    });
  });

  document.querySelectorAll("[data-employee-form] [type='tel']").forEach((input) => {
    input.addEventListener("input", () => {
      const phone = input.value.trim();
      const valid = !phone || /^\+?[0-9().\-\s]{7,30}$/.test(phone);
      input.setCustomValidity(valid ? "" : "Enter a valid phone number.");
    });
  });
})();
