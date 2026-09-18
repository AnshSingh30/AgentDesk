import { Suspense } from "react";

import { ForgotPasswordForm } from "@/components/auth-forms";

export default function Page() {
  return (
    <Suspense>
      <ForgotPasswordForm />
    </Suspense>
  );
}
