import { Suspense } from "react";

import { SignupForm } from "@/components/auth-forms";

export default function Page() {
  return (
    <Suspense>
      <SignupForm />
    </Suspense>
  );
}
