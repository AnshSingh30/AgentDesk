import { Suspense } from "react";

import { ResetPasswordForm } from "@/components/auth-forms";

export default function Page() {
  return (
    <Suspense>
      <ResetPasswordForm />
    </Suspense>
  );
}
