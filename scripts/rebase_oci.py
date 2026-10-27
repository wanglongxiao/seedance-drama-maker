"""Reuse exactly matching published runtime layers without downloading their blobs."""
import copy
import hashlib
import io
import json
import subprocess
import tarfile


def _boundary(config):
    history = config["history"]
    matches = [i for i, h in enumerate(history)
               if h.get("created_by") == "COPY app ./app # buildkit"
               and not h.get("empty_layer")]
    if len(matches) != 1:
        raise ValueError("Expected one application boundary")
    index = matches[0]
    count = sum(not h.get("empty_layer", False) for h in history[:index])
    if len(config["rootfs"]["diff_ids"]) != sum(
            not h.get("empty_layer", False) for h in history):
        raise ValueError("Image history and rootfs disagree")
    return index, count


def _encode(value):
    return json.dumps(value, separators=(",", ":")).encode()


def _descriptor(data, media_type):
    return {"mediaType": media_type, "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size": len(data)}


def rebase_runtime(source, target, published_image, published_commit, release_commit):
    def inspect(*arguments):
        result = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", published_image, *arguments],
            capture_output=True, check=True)
        return json.loads(result.stdout)

    old_manifest = inspect("--raw")
    old_config = inspect("--format", "{{json .Image}}")
    with tarfile.open(source) as archive:
        def read(descriptor):
            data = archive.extractfile("blobs/" + descriptor["digest"].replace(":", "/")).read()
            if _descriptor(data, descriptor["mediaType"])["digest"] != descriptor["digest"]:
                raise ValueError("Archive blob checksum mismatch")
            if len(data) != descriptor["size"]:
                raise ValueError("Archive blob size mismatch")
            return json.loads(data)

        index = json.load(archive.extractfile("index.json"))
        candidates = [d for d in index["manifests"]
                      if d.get("platform") == {"architecture": "amd64", "os": "linux"}]
        if len(candidates) != 1:
            raise ValueError("Expected one linux/amd64 image")
        manifest = read(candidates[0])
        config = read(manifest["config"])
        old_h, old_n = _boundary(old_config)
        new_h, new_n = _boundary(config)
        if (old_h, old_n) != (new_h, new_n) or old_n < 2:
            raise ValueError("Runtime boundaries differ")
        for item in (old_config, config):
            if (item["architecture"], item["os"]) != ("amd64", "linux"):
                raise ValueError("Runtime platform differs")
        for item, commit in ((old_config, published_commit), (config, release_commit)):
            if item["config"].get("Labels", {}).get("org.opencontainers.image.revision") != commit:
                raise ValueError("Image revision differs from validated commit")
        old_settings, new_settings = copy.deepcopy(old_config["config"]), copy.deepcopy(config["config"])
        for settings in (old_settings, new_settings):
            settings.get("Labels", {}).pop("org.opencontainers.image.revision", None)
        if old_settings != new_settings:
            raise ValueError("Runtime configuration changed")
        histories = [[(h.get("created_by"), bool(h.get("empty_layer")))
                      for h in c["history"]] for c in (old_config, config)]
        if histories[0] != histories[1]:
            raise ValueError("Build instructions changed")
        for m, c in ((old_manifest, old_config), (manifest, config)):
            if len(m["layers"]) != len(c["rootfs"]["diff_ids"]):
                raise ValueError("Manifest and rootfs disagree")
        if (old_manifest["layers"][:old_n] != manifest["layers"][:new_n]
                or old_config["rootfs"]["diff_ids"][:old_n]
                != config["rootfs"]["diff_ids"][:new_n]):
            raise ValueError("Runtime dependencies differ")
        # Every reused runtime layer must match exactly.
        manifest["layers"][:new_n] = old_manifest["layers"][:old_n]
        config["rootfs"]["diff_ids"][:new_n] = old_config["rootfs"]["diff_ids"][:old_n]
        config["history"][:new_h] = old_config["history"][:old_h]
        config_bytes = _encode(config)
        manifest["config"] = _descriptor(config_bytes, manifest["config"]["mediaType"])
        manifest_bytes = _encode(manifest)
        descriptor = _descriptor(manifest_bytes, manifest["mediaType"])
        descriptor["platform"] = {"architecture": "amd64", "os": "linux"}
        index["manifests"] = [descriptor]
        with tarfile.open(target, "w") as output:
            def add(name, data):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))

            add("oci-layout", _encode({"imageLayoutVersion": "1.0.0"}))
            add("index.json", _encode(index))
            add("blobs/" + descriptor["digest"].replace(":", "/"), manifest_bytes)
            add("blobs/" + manifest["config"]["digest"].replace(":", "/"), config_bytes)
            for layer in manifest["layers"][new_n:]:
                name = "blobs/" + layer["digest"].replace(":", "/")
                member = archive.getmember(name)
                if member.size != layer["size"]:
                    raise ValueError("Application layer size mismatch")
                output.addfile(member, archive.extractfile(member))
        print("Reused published runtime layers:", old_n, "; application upload bytes:",
              sum(d["size"] for d in manifest["layers"][new_n:]), flush=True)
