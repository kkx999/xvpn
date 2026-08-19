package com.xvpn.android;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

final class NodeCatalog {
    final List<Country> countries = new ArrayList<>();
    int total;

    static NodeCatalog fromBootstrap(JSONObject bootstrap) {
        NodeCatalog out = new NodeCatalog();
        JSONObject nodes = bootstrap.optJSONObject("nodes");
        if (nodes == null) return out;
        out.total = nodes.optInt("total", 0);
        JSONArray countries = nodes.optJSONArray("countries");
        if (countries == null) return out;
        for (int i = 0; i < countries.length(); i++) {
            JSONObject c = countries.optJSONObject(i);
            if (c == null) continue;
            Country country = new Country();
            country.name = c.optString("country", "其他");
            country.code = c.optString("country_code", "ZZ");
            country.flag = c.optString("flag_emoji", "🌐");
            country.sortOrder = c.optInt("sort_order", 999999);
            JSONArray nodesArray = c.optJSONArray("nodes");
            if (nodesArray != null) {
                for (int j = 0; j < nodesArray.length(); j++) {
                    JSONObject n = nodesArray.optJSONObject(j);
                    if (n == null) continue;
                    Node node = new Node();
                    node.id = n.optInt("id", 0);
                    node.name = n.optString("display_name", n.optString("name", "节点"));
                    node.country = n.optString("country", country.name);
                    node.countryCode = n.optString("country_code", country.code);
                    node.region = n.optString("region", "");
                    node.protocol = n.optString("protocol", "");
                    node.config = n.optString("config", "");
                    node.sortOrder = n.optInt("sort_order", j);
                    node.flag = country.flag;
                    country.nodes.add(node);
                }
            }
            out.countries.add(country);
        }
        return out;
    }

    Node firstNode() {
        for (Country c : countries) if (!c.nodes.isEmpty()) return c.nodes.get(0);
        return null;
    }

    Node find(int id) {
        if (id <= 0) return null;
        for (Country c : countries) for (Node n : c.nodes) if (n.id == id) return n;
        return null;
    }

    static final class Country {
        String name;
        String code;
        String flag;
        int sortOrder;
        final List<Node> nodes = new ArrayList<>();
    }

    static final class Node {
        int id;
        int sortOrder;
        String name;
        String country;
        String countryCode;
        String region;
        String protocol;
        String config;
        String flag;
    }
}
