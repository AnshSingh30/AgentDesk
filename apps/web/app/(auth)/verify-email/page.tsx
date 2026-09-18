import { Suspense } from "react";

import { VerifyEmail } from "@/components/auth-forms";

export default function Page() {
  return (
    <Suspense>
      <VerifyEmail />
    </Suspense>
  );
}
