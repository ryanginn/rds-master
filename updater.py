"""
    RDS Master Updater Module
    Copyright (C) 2026 Rían Mac Guinneál (FMDX.ie)

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""

import os
import sys
import json
import shutil
import zipfile
import hashlib
import tempfile
from datetime import datetime
from pathlib import Path


class UpdateManager:
    """Manages application updates with backup and validation"""

    BACKUP_DIR = "backups"
    DATASETS_FILE = "datasets.json"
    REQUIRED_FILES = ["app.py"]  # Files that must be present in update
    # Never overwritten by an update: this is the operator's, not the author's.
    KEEP = {"datasets.json", "config.ini", "audit.log"}

    def __init__(self, app_dir=None, backup_dir=None):
        """Initialize the update manager

        Args:
            app_dir: the directory the program's own files live in. It defaults
                to the current directory, which is only right when the program
                was started from it - a service or a scheduled task is usually
                somewhere else, so the caller passes the real one.
            backup_dir: where to keep backups. Program Files is not writable,
                so the caller passes the data directory instead.
        """
        self.app_dir = Path(app_dir) if app_dir else Path.cwd()
        self.backup_dir = Path(backup_dir) if backup_dir else self.app_dir / self.BACKUP_DIR
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[Updater] cannot create {self.backup_dir}: {exc}")

    def writable(self):
        """Whether the program's own files can be replaced in place."""
        probe = self.app_dir / ".update-write-test"
        try:
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:
            return False

    def create_backup(self):
        """Create a backup of critical files before update

        Returns:
            dict: Backup information with paths and checksums
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{timestamp}"
        backup_path = self.backup_dir / backup_name
        backup_path.mkdir(exist_ok=True)

        backup_info = {
            "timestamp": timestamp,
            "backup_path": str(backup_path),
            "files": {}
        }

        # Backup datasets.json
        datasets_src = self.app_dir / self.DATASETS_FILE
        if datasets_src.exists():
            datasets_dst = backup_path / self.DATASETS_FILE
            shutil.copy2(datasets_src, datasets_dst)
            backup_info["files"]["datasets.json"] = {
                "checksum": self._calculate_checksum(datasets_src),
                "size": datasets_src.stat().st_size
            }

        # Backup current app.py
        app_src = self.app_dir / "app.py"
        if app_src.exists():
            app_dst = backup_path / "app.py"
            shutil.copy2(app_src, app_dst)
            backup_info["files"]["app.py"] = {
                "checksum": self._calculate_checksum(app_src),
                "size": app_src.stat().st_size
            }

        # Save backup info
        info_file = backup_path / "backup_info.json"
        with open(info_file, 'w') as f:
            json.dump(backup_info, f, indent=2)

        # Create marker file for post-update validation
        marker_file = self.app_dir / ".update_marker"
        with open(marker_file, 'w') as f:
            json.dump({"backup_path": str(backup_path)}, f)

        print(f"[Updater] Backup created: {backup_path}")
        return backup_info

    def validate_zip(self, zip_path):
        """Validate update zip file

        Args:
            zip_path: Path to the zip file

        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            # Check if file exists
            if not os.path.exists(zip_path):
                return False, "Zip file not found"

            # Check if it's a valid zip
            if not zipfile.is_zipfile(zip_path):
                return False, "Invalid zip file"

            # Check zip contents
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Get list of files in zip
                file_list = zf.namelist()

                # Check for required files
                for required in self.REQUIRED_FILES:
                    if required not in file_list and not any(f.endswith(required) for f in file_list):
                        return False, f"Missing required file: {required}"

                # Check for path traversal attempts
                for filename in file_list:
                    if filename.startswith('/') or '..' in filename:
                        return False, f"Invalid file path detected: {filename}"

                # Test zip integrity
                bad_file = zf.testzip()
                if bad_file:
                    return False, f"Corrupted file in zip: {bad_file}"

            return True, "Zip file is valid"

        except Exception as e:
            return False, f"Validation error: {str(e)}"

    @staticmethod
    def _inside(target, root):
        """Whether a path stays within the program's own directory.

        A member like ../../x would otherwise be written anywhere at all.
        validate_zip refuses those, but extraction does not rely on having
        been called after it.
        """
        try:
            resolved = Path(target).resolve()
        except OSError:
            return False
        try:
            return resolved.is_relative_to(root)
        except AttributeError:              # Python 3.8
            return str(resolved).startswith(str(root))

    def extract_update(self, zip_path):
        """Extract update files to application directory

        Args:
            zip_path: Path to the validated zip file

        Returns:
            tuple: (success, message, extracted_files)
        """
        try:
            extracted_files = []

            root = self.app_dir.resolve()
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Every path is checked before anything is written, so a zip
                # with one bad member does not leave half an update behind.
                for member in zf.namelist():
                    if member.endswith('/') or Path(member).name in self.KEEP:
                        continue
                    if not self._inside(self.app_dir / member, root):
                        return False, ("Refusing to write outside the program "
                                       f"directory: {member}"), []

                for member in zf.namelist():
                    # Skip directories
                    if member.endswith('/'):
                        continue

                    # The operator's own files are not part of an update, even
                    # when the zip happens to contain them.
                    if Path(member).name in self.KEEP:
                        print(f"[Updater] left alone: {member}")
                        continue

                    # Extract to app directory
                    target_path = self.app_dir / member

                    # Create parent directories if needed
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    # Extract file
                    with zf.open(member) as source, open(target_path, 'wb') as target:
                        shutil.copyfileobj(source, target)

                    extracted_files.append(member)
                    print(f"[Updater] Extracted: {member}")

            return True, f"Extracted {len(extracted_files)} files", extracted_files

        except Exception as e:
            return False, f"Extraction error: {str(e)}", []

    def check_post_update_validation(self):
        """Check if update validation is needed after restart

        Returns:
            dict: Validation result with status and details
        """
        marker_file = self.app_dir / ".update_marker"

        if not marker_file.exists():
            return {"validation_needed": False}

        try:
            # Read marker file
            with open(marker_file, 'r') as f:
                marker_data = json.load(f)

            backup_path = Path(marker_data["backup_path"])

            # Validate datasets.json
            datasets_current = self.app_dir / self.DATASETS_FILE
            datasets_backup = backup_path / self.DATASETS_FILE

            validation_result = {
                "validation_needed": True,
                "backup_path": str(backup_path),
                "datasets_ok": False,
                "can_delete_backup": False
            }

            if datasets_current.exists() and datasets_backup.exists():
                # Compare checksums
                current_checksum = self._calculate_checksum(datasets_current)
                backup_checksum = self._calculate_checksum(datasets_backup)

                if current_checksum == backup_checksum:
                    validation_result["datasets_ok"] = True
                    validation_result["can_delete_backup"] = True
                    print(f"[Updater] Dataset validation passed - checksums match")
                else:
                    # Check if datasets can be loaded
                    try:
                        with open(datasets_current, 'r') as f:
                            json.load(f)
                        validation_result["datasets_ok"] = True
                        validation_result["can_delete_backup"] = True
                        print(f"[Updater] Dataset validation passed - valid JSON structure")
                    except:
                        validation_result["datasets_ok"] = False
                        print(f"[Updater] Dataset validation failed - corrupted file")

            # Remove marker file
            marker_file.unlink()

            return validation_result

        except Exception as e:
            print(f"[Updater] Validation check error: {e}")
            return {"validation_needed": False, "error": str(e)}

    def cleanup_backup(self, backup_path):
        """Remove backup after successful validation

        Args:
            backup_path: Path to the backup directory to remove
        """
        try:
            backup_path = Path(backup_path)
            if backup_path.exists():
                shutil.rmtree(backup_path)
                print(f"[Updater] Backup cleaned up: {backup_path}")
                return True
        except Exception as e:
            print(f"[Updater] Backup cleanup error: {e}")
            return False

    def restore_backup(self, backup_path):
        """Restore from backup after failed update

        Args:
            backup_path: Path to the backup directory

        Returns:
            tuple: (success, message)
        """
        try:
            backup_path = Path(backup_path)

            if not backup_path.exists():
                return False, "Backup path not found"

            # Restore datasets.json
            datasets_backup = backup_path / self.DATASETS_FILE
            if datasets_backup.exists():
                datasets_current = self.app_dir / self.DATASETS_FILE
                shutil.copy2(datasets_backup, datasets_current)
                print(f"[Updater] Restored datasets.json from backup")

            # Restore app.py
            app_backup = backup_path / "app.py"
            if app_backup.exists():
                app_current = self.app_dir / "app.py"
                shutil.copy2(app_backup, app_current)
                print(f"[Updater] Restored app.py from backup")

            return True, "Backup restored successfully"

        except Exception as e:
            return False, f"Restore error: {str(e)}"

    def restart_application(self, restart=None):
        """Restart the application process.

        `restart` is the application's own restart, which knows whether a
        service manager will bring it back, how to start a replacement without
        inheriting broken handles, and how to wait for the port to be released.
        os.execv knows none of that and cannot restart a frozen build at all,
        so it is only the fallback for a plain script run by hand.
        """
        print("[Updater] Restarting application...")
        sys.stdout.flush()
        sys.stderr.flush()

        if restart is not None:
            restart()
            return

        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _calculate_checksum(self, file_path):
        """Calculate SHA256 checksum of a file

        Args:
            file_path: Path to the file

        Returns:
            str: Hex digest of checksum
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def get_available_backups(self):
        """Get list of available backups

        Returns:
            list: List of backup info dictionaries
        """
        backups = []

        if not self.backup_dir.exists():
            return backups

        for backup_folder in sorted(self.backup_dir.iterdir(), reverse=True):
            if backup_folder.is_dir():
                info_file = backup_folder / "backup_info.json"
                if info_file.exists():
                    try:
                        with open(info_file, 'r') as f:
                            backup_info = json.load(f)
                            backup_info["name"] = backup_folder.name
                            backups.append(backup_info)
                    except:
                        pass

        return backups
