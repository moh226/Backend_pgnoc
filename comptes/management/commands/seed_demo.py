"""Jeu de données de démonstration pour tester l'API sur Swagger.

Crée (idempotent) : une SGI, son Admin SGI, son Agent SGI, l'Admin
Général, un investisseur et la convention tarifaire PDF de la SGI.

Usage :
    ./env/bin/python manage.py seed_demo

Mot de passe commun : MotDePasse-Demo-2026!
"""
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction

from comptes.models import Role, Utilisateur
from sgi.models import ConventionTarifaire, SGI

MOT_DE_PASSE = "MotDePasse-Demo-2026!"

CREDENTIALS = {
    "admin-general@demo.pgnoc": "Admin Général",
    "admin-sgi@demo.pgnoc": "Admin SGI",
    "agent-sgi@demo.pgnoc": "Agent SGI",
    "investisseur@demo.pgnoc": "Investisseur",
}


def _construire_pdf_demo():
    """Mini-PDF d'une page, valide et de magic bytes corrects (`%PDF`)."""
    stream = b"BT /F1 20 Tf 72 760 Td (Convention tarifaire PGNOC-TI) Tj ET\n"
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    contenu = bytearray(b"%PDF-1.4\n")
    offsets = []
    for numero, objet in enumerate(objets, start=1):
        offsets.append(len(contenu))
        contenu += (
            f"{numero} 0 obj\n".encode() + objet + b"\nendobj\n"
        )
    position_xref = len(contenu)
    contenu += f"xref\n0 {len(offsets) + 1}\n".encode()
    contenu += b"0000000000 65535 f \n"
    for offset in offsets:
        contenu += f"{offset:010d} 00000 n \n".encode()
    contenu += (
        f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R >>\n"
        f"startxref\n{position_xref}\n%%EOF\n".encode()
    )
    return bytes(contenu)


class Command(BaseCommand):
    help = "Crée le jeu de données de démo pour tester l'API sur Swagger."

    @transaction.atomic
    def handle(self, *args, **options):
        roles = {
            code: Role.objects.get(code=code.value)
            for code in Role.Code
            if Role.objects.filter(code=code.value).exists()
        }
        if len(roles) < 4:
            self.stderr.write(
                self.style.ERROR(
                    "Rôles absents : lancer `manage.py migrate` d'abord."
                )
            )
            return

        sgi, cree = SGI.objects.get_or_create(
            code_sgi="DEMO",
            defaults={
                "nom": "SGI Démo UEMOA",
                "est_active": True,
            },
        )
        self.stdout.write(
            f"{'Créée' if cree else 'Déjà présente'} : SGI {sgi.nom} "
            f"({sgi.code_sgi}) — id={sgi.id}"
        )

        par_email = {
            "admin-general@demo.pgnoc": (roles[Role.Code.ADMIN_GENERAL.value], None),
            "admin-sgi@demo.pgnoc": (roles[Role.Code.ADMIN_SGI.value], sgi),
            "agent-sgi@demo.pgnoc": (roles[Role.Code.AGENT_SGI.value], sgi),
            "investisseur@demo.pgnoc": (roles[Role.Code.INVESTISSEUR.value], None),
        }
        for email, (role, sgi_liee) in par_email.items():
            libelle = CREDENTIALS[email]
            _, cree = Utilisateur.objects.get_or_create(
                email=email,
                defaults={
                    "role": role,
                    "sgi": sgi_liee,
                    "nom": libelle,
                    "is_active": True,
                },
            )
            if cree:
                utilisateur = Utilisateur.objects.get(email=email)
                utilisateur.set_password(MOT_DE_PASSE)
                utilisateur.save(update_fields=["password"])
            self.stdout.write(
                f"{'Créé' if cree else 'Déjà présent'} : {libelle} ({email})"
            )

        convention, cree = ConventionTarifaire.objects.get_or_create(
            sgi_id=sgi.id,
        )
        if cree or not convention.fichier_pdf:
            convention.titre = "Convention tarifaire démo"
            convention.fichier_pdf.save(
                "convention-demo.pdf",
                ContentFile(_construire_pdf_demo()),
                save=False,
            )
            convention.save()
        self.stdout.write(
            f"{'Créée' if cree else 'Déjà présente'} : convention tarifaire "
            f"(PDF {'posé' if convention.fichier_pdf else 'absent'})"
        )

        self.stdout.write(self.style.SUCCESS("\n— Données de démo prêtes —"))
        self.stdout.write(f"Mot de passe commun : {MOT_DE_PASSE}")
        for email in CREDENTIALS:
            self.stdout.write(f"  {email}")
        self.stdout.write("Ouvrir : http://127.0.0.1:8000/api/docs/")