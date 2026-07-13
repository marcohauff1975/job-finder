"""
Admin UI for the Job Finder product's own "Jobfinder Admin" tab in
streamlit_app.py - user metrics, tier management, password reset,
delete user.

Pulled out of streamlit_app.py so that file's admin section reflects the
actual product boundary already true at runtime: this code only ever
touches auth.py/reporting.py (Job Finder's own modules), never anything
under req2prod/ or the sibling req2prod_*.py files - see req2prod/admin_ui.py for
that product's equivalent. Pure extraction: the function body below is
unchanged from streamlit_app.py's former inline `with tab_overview:`
block, just relocated and wrapped in a def, with UNLIMITED_USER
parameterized (it stays defined in streamlit_app.py, since it's also
used in that file's own untouched Job Finder path).
"""

import streamlit as st

from auth import delete_user, set_user_password
from reporting import VALID_TIERS, delete_user_data, get_report, get_serper_balance, set_user_tier


def render_overview_tab(unlimited_user: str) -> None:
    """The "Jobfinder Admin" tab: registered users/CVs-generated metrics,
    Serper balance, per-user tier management, password reset, and
    delete user."""
    st.caption("[yourmagicaljobfinder.online](https://www.yourmagicaljobfinder.online)")

    report = get_report()
    st.metric("Registered users", report["registered_users"])
    st.metric("CVs generated", report["cvs_total"])
    st.caption(
        f"{report['cvs_tailored']} tailored for a specific job, "
        f"{report['cvs_format']} format rebuilds."
    )

    serper_balance = get_serper_balance()
    st.metric(
        "Serper credits remaining",
        serper_balance if serper_balance is not None else "unavailable",
    )

    st.markdown("#### Per user")
    st.caption(
        "Tier controls which Claude model each user's agents use - see "
        "job_search.py's TIER_HIGH_MODEL_AGENTS. Test accounts (@example.com) "
        "always run on free regardless of what's set here."
    )
    edited_rows = st.data_editor(
        [
            {
                "Email": row["email"],
                "Tailored for a job": row["tailored"],
                "Format rebuilds": row["format"],
                "Total": row["total"],
                "Tier": row["tier"],
            }
            for row in report["per_user"]
        ],
        column_config={
            "Tier": st.column_config.SelectboxColumn(
                options=list(VALID_TIERS), required=True
            ),
        },
        disabled=["Email", "Tailored for a job", "Format rebuilds", "Total"],
        use_container_width=True,
        hide_index=True,
        key="tier_editor",
    )
    if st.button("Save tier changes"):
        for row in edited_rows:
            set_user_tier(row["Email"], row["Tier"])
        st.success("Tiers saved.")
        st.rerun()

    user_emails = [row["email"] for row in report["per_user"]]

    st.markdown("#### Reset a user's password")
    with st.form("admin_reset_password_form"):
        reset_email = st.selectbox("User", user_emails, key="reset_pw_user")
        new_password = st.text_input(
            "New password", type="password", key="reset_pw_value"
        )
        if st.form_submit_button("Reset password"):
            if len(new_password) < 4:
                st.error("Password must be at least 4 characters.")
            else:
                set_user_password(reset_email, new_password)
                st.success(f"Password reset for {reset_email}.")

    st.markdown("#### Delete a user")
    st.caption(
        "Removes the account and all their data (resume, tailored resumes, "
        "search history) - this can't be undone."
    )
    with st.form("admin_delete_user_form"):
        delete_email = st.selectbox("User", user_emails, key="delete_user_select")
        confirm_email = st.text_input(
            "Type the user's email to confirm deletion", key="delete_confirm"
        )
        if st.form_submit_button("Delete user"):
            if delete_email == unlimited_user:
                st.error("Can't delete the admin account.")
            elif confirm_email != delete_email:
                st.error("Confirmation email doesn't match - user not deleted.")
            else:
                delete_user(delete_email)
                delete_user_data(delete_email)
                st.success(f"Deleted {delete_email} and all their data.")
                st.rerun()
